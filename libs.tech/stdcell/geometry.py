"""Area-first canonical ICPS2023_5 1M transistor-cell geometry.

Rows are built from canonical MOS mask geometry and connected terminal paths.
The generator never emits POLY routing, upper metal, body ties, or a direct
TiN-to-diffusion contact.  DRC/LVS/PEX remain independent acceptance gates.
"""
from __future__ import annotations

import heapq
from bisect import bisect_right
import math
from itertools import permutations
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

try:
    import klayout.db as db
except ImportError:  # pragma: no cover - supplied by the pinned KLayout toolchain
    from klayout import db  # type: ignore

DBU = 0.001
DIFF, TIN, CNT, ML1, TEXT, NWL, PAREA, NAREA = 3, 5, 7, 8, 13, 1, 18, 19
MIN_W, MIN_L = 13.0, 6.0
CONTACT, CUT, GATE_OVERLAP = 13.0, 5.0, 2.0
GATE_CONTACT = 13.0  # Native PCell landing; CNT enclosure minimum alone is 10um
N_GATE_CONTACT_OFFSET = 26.0
P_GATE_CONTACT_OFFSET = 26.0
COMPACT_GATE_CONTACT_OFFSET = 11.5
VIA_CONTACT = 10.0  # Strict CNT_TIN enclosure: 5um cut + 2.5um on each side
ML1_WIDTH, ML1_SPACE, TIN_WIDTH, TIN_SPACE = 6.0, 4.0, 6.0, 6.0
ML1_ROUTE_DIRECTIONS = ((1, 0), (-1, 0), (0, 1), (0, -1))
TIN_ROUTE_DIRECTIONS = ((1, 0), (-1, 0), (0, 1), (0, -1))

CONTACT_SPAN = 35.0  # PCell: 13um contact + 6um gate + 2*8um gate bay
Rect = Tuple[float, float, float, float]
Point = Tuple[float, float]


class GeometryError(ValueError):
    """Raised when an explicit topology cannot fit or route at the requested height."""


def _finite(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise GeometryError(f"{field} must be finite")
    return number


def _snap(value: float) -> float:
    return round(float(value) / DBU) * DBU


def _rect(x1: float, y1: float, x2: float, y2: float) -> Rect:
    return (_snap(min(x1, x2)), _snap(min(y1, y2)),
            _snap(max(x1, x2)), _snap(max(y1, y2)))


def _layer(layout: db.Layout, number: int) -> int:
    return layout.layer(db.LayerInfo(number, 0))


def _put_box(layout: db.Layout, cell: db.Cell, layer: int, rect: Rect) -> None:
    cell.shapes(layer).insert(db.Box(*(round(v / DBU) for v in rect)))


def _put_text(layout: db.Layout, cell: db.Cell, layer: int, name: str, point: Point) -> None:
    origin = db.Point(round(point[0] / DBU), round(point[1] / DBU))
    cell.shapes(layer).insert(db.Text(name, db.Trans(db.Trans.R0, origin)))


def _device_records(spec: Mapping[str, Any], fingers: int) -> List[Dict[str, Any]]:
    raw = spec.get("mos")
    if not isinstance(raw, list) or not raw:
        raise GeometryError("cell specification has no MOS network")
    records: List[Dict[str, Any]] = []
    names: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise GeometryError(f"MOS entry {index} is not a mapping")
        polarity = str(item.get("polarity", "")).casefold()
        if polarity not in ("n", "p"):
            raise GeometryError(f"MOS entry {index} has invalid polarity")
        w = _finite(item.get("w_um", 24.0), f"MOS {index} width")
        length = _finite(item.get("l_um", 6.0), f"MOS {index} length")
        if w < MIN_W or length < MIN_L:
            raise GeometryError(f"MOS {item.get('name', index)} needs W>=13um and L>=6um; got W={w:g}, L={length:g}")
        for terminal in ("d", "g", "s"):
            if not isinstance(item.get(terminal), str) or not item[terminal]:
                raise GeometryError(f"MOS entry {index} has no {terminal} net")
        base = str(item.get("name", f"M{index}"))
        for finger in range(fingers):
            name = f"{base}_f{finger}" if fingers > 1 else base
            if name in names:
                raise GeometryError(f"duplicate MOS name {name}")
            names.add(name)
            records.append({"name": name, "polarity": polarity, "d": str(item["d"]),
                            "g": str(item["g"]), "s": str(item["s"]),
                            "w_um": _snap(w), "l_um": _snap(length),
                            "source_index": index, "finger": finger})
    return records


def _euler_trail(devices: Sequence[Mapping[str, Any]], reverse: bool = False):
    """Return (ordered edges, vertex walk) when one continuous active island is possible."""
    if not devices:
        return None
    adjacency: Dict[str, List[int]] = defaultdict(list)
    for index, device in enumerate(devices):
        left, right = device["d"], device["s"]
        adjacency[left].append(index)
        adjacency[right].append(index)
    odd = sorted(node for node, edges in adjacency.items() if len(edges) % 2)
    if len(odd) not in (0, 2):
        return None
    starts = odd if odd else sorted(adjacency)
    start = starts[0]
    for value in adjacency.values():
        value.sort(key=lambda i: str(devices[i]["name"]), reverse=reverse)
    used: set[int] = set()
    stack: List[Tuple[str, int | None]] = [(start, None)]
    vertices: List[str] = []
    edges: List[int] = []
    while stack:
        node, incoming = stack[-1]
        choices = [edge for edge in adjacency[node] if edge not in used]
        if choices:
            edge = choices[0]
            used.add(edge)
            device = devices[edge]
            other = device["s"] if device["d"] == node else device["d"]
            stack.append((other, edge))
        else:
            vertices.append(node)
            stack.pop()
            if incoming is not None:
                edges.append(incoming)
    edges.reverse()
    vertices.reverse()
    if len(edges) != len(devices) or len(vertices) != len(devices) + 1:
        return None
    return [devices[index] for index in edges], vertices


def _alignment(p_devices: Sequence[Mapping[str, Any]], n_devices: Sequence[Mapping[str, Any]]):
    """LCS-align equal gate nets, inserting explicit active-row gaps."""
    p_gates = [d["g"] for d in p_devices]
    n_gates = [d["g"] for d in n_devices]
    dp = [[0] * (len(n_gates) + 1) for _ in range(len(p_gates) + 1)]
    for i in range(len(p_gates) - 1, -1, -1):
        for j in range(len(n_gates) - 1, -1, -1):
            dp[i][j] = (1 + dp[i+1][j+1] if p_gates[i] == n_gates[j]
                        else max(dp[i+1][j], dp[i][j+1]))
    columns: List[Tuple[int | None, int | None]] = []
    i = j = 0
    while i < len(p_gates) or j < len(n_gates):
        if i < len(p_gates) and j < len(n_gates) and p_gates[i] == n_gates[j]:
            columns.append((i, j)); i += 1; j += 1
        elif j >= len(n_gates) or (i < len(p_gates) and dp[i+1][j] >= dp[i][j+1]):
            columns.append((i, None)); i += 1
        else:
            columns.append((None, j)); j += 1
    return columns


def _point_key(point: Point) -> Tuple[int, int]:
    return (round(point[0] * 2), round(point[1] * 2))


def _point_from_key(key: Tuple[int, int]) -> Point:
    return (_snap(key[0] / 2), _snap(key[1] / 2))




def _route_ml1(start: Point, goal: Point, net: str,
               shapes: Sequence[Tuple[Rect, str, str]], width: float, height: float) -> List[Point]:
    source, target = _point_key(start), _point_key(goal)
    if source == target:
        return [start]
    lo, hi = 6, round((width - 3.0) * 2)
    by, ty = 6, round((height - 3.0) * 2)
    blocked_rows=defaultdict(list)
    for shape_index,(rect,owner,kind) in enumerate(shapes):
        if owner==net and kind in {"pad","via","wire"}: continue
        margin=ML1_WIDTH/2+ML1_SPACE
        x1,y1,x2,y2=rect
        xlo=math.ceil((x1-margin)*2-1e-9)
        xhi=math.floor((x2+margin)*2+1e-9)
        ylo=math.ceil((y1-margin)*2-1e-9)
        yhi=math.floor((y2+margin)*2+1e-9)
        for ykey in range(ylo,yhi+1):
            blocked_rows[ykey].append((xlo,xhi,shape_index))
    for intervals in blocked_rows.values():
        intervals.sort()
    def blocked(key,ignored=frozenset()):
        intervals=blocked_rows.get(key[1],())
        index=bisect_right(intervals,(key[0],math.inf,math.inf))-1
        for cursor in range(index,-1,-1):
            xlo,xhi,shape_index=intervals[cursor]
            if shape_index not in ignored and key[0]<=xhi:
                return True
        return False
    def in_bounds(key):
        return lo<=key[0]<=hi and by<=key[1]<=ty
    def endpoint(point):
        for index,(rect,owner,kind) in enumerate(shapes):
            if owner==net and kind in {"pad","via"} and \
                    rect[0]<=point[0]<=rect[2] and rect[1]<=point[1]<=rect[3]:
                margin=ML1_WIDTH/2+ML1_SPACE+0.5
                candidates=[
                    (_snap(point[0]),_snap(rect[3]+margin)),
                    (_snap(point[0]),_snap(rect[1]-margin)),
                    (_snap(rect[2]+margin),_snap(point[1])),
                    (_snap(rect[0]-margin),_snap(point[1]))]
                return index,candidates
        return None,[point]
    source_pad,source_exits=endpoint(start)
    target_pad,target_exits=endpoint(goal)
    def access_path(point,exit_point,ignored):
        first,last=_point_key(point),_point_key(exit_point)
        if first[0]!=last[0] and first[1]!=last[1]: return None
        dx=(last[0]>first[0])-(last[0]<first[0])
        dy=(last[1]>first[1])-(last[1]<first[1])
        keys=[]; current=first
        while True:
            if not in_bounds(current) or blocked(current,ignored): return None
            keys.append(_point_from_key(current))
            if current==last: return keys
            current=(current[0]+dx,current[1]+dy)
    def route_core(core_start,core_goal):
        first,last=_point_key(core_start),_point_key(core_goal)
        if first==last: return [core_start]
        def valid(key):
            return in_bounds(key) and not blocked(key)
        if not valid(first) or not valid(last): return None
        queue=[(0.0,0,first)]
        previous={first:None}; distance={first:0.0}; serial=0
        while queue:
            _,_,current=heapq.heappop(queue)
            if current==last:
                path=[]; cursor=current
                while cursor is not None:
                    path.append(_point_from_key(cursor)); cursor=previous[cursor]
                return list(reversed(path))
            for dx,dy in ML1_ROUTE_DIRECTIONS:
                nxt=(current[0]+dx,current[1]+dy)
                if nxt in previous or not valid(nxt): continue
                distance[nxt]=distance[current]+0.5
                previous[nxt]=current; serial+=1
                estimate=(abs(nxt[0]-last[0])+abs(nxt[1]-last[1]))/2
                heapq.heappush(queue,(distance[nxt]+estimate,serial,nxt))
        return None
    best=None; best_length=float("inf")
    source_ignore=frozenset(() if source_pad is None else (source_pad,))
    target_ignore=frozenset(() if target_pad is None else (target_pad,))
    for source_exit in source_exits:
        source_stub=access_path(start,source_exit,source_ignore)
        if source_stub is None: continue
        for target_exit in target_exits:
            target_stub=access_path(goal,target_exit,target_ignore)
            if target_stub is None: continue
            core=route_core(source_exit,target_exit)
            if core is None: continue
            path=source_stub+core[1:]+list(reversed(target_stub[:-1]))
            length=sum(abs(a[0]-b[0])+abs(a[1]-b[1])
                       for a,b in zip(path,path[1:]))
            if length<best_length:
                best,best_length=path,length
    if best is None:
        raise GeometryError(f"no legal ML1 route for net {net} between {start} and {goal}")
    return best


def _blocked_tin(point: Point, net: str,
                 shapes: Sequence[Tuple[Rect, str, str]],
                 active: Sequence[Rect], access: Sequence[Point],
                 allow_same_net_reuse: bool,
                 allow_gate_in_active: bool) -> bool:
    x, y = point
    active_margin = 0.0 if allow_gate_in_active else TIN_WIDTH/2+0.5
    in_active = any(x1-active_margin <= x <= x2+active_margin and
                    y1-active_margin <= y <= y2+active_margin
                    for x1, y1, x2, y2 in active)
    on_own_gate = any(owner == net and kind == "gate" and
                      rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]
                      for rect, owner, kind in shapes)
    if in_active and (not on_own_gate or not allow_gate_in_active):
        return True
    for rect, owner, kind in shapes:
        if owner == net:
            endpoint_access = any(
                rect[0] <= ax <= rect[2] and rect[1] <= ay <= rect[3]
                for ax, ay in access)
            gate_escape = kind == "gate" and any(
                rect[0]-5.0 <= ax <= rect[2]+5.0 and
                rect[1]-5.0 <= ay <= rect[3]+5.0
                for ax, ay in access)
            # Same-net wire branches merge; gate and pad spacing remain enforced.
            if allow_same_net_reuse or endpoint_access or gate_escape:
                continue
        margin = 9.5  # 3um half-width + 6um TiN spacing, with grid margin
        x1, y1, x2, y2 = rect
        if x1-margin <= x <= x2+margin and y1-margin <= y <= y2+margin:
            return True
    return False

def _route_tin(start: Point, goal: Point, net: str,
               shapes: Sequence[Tuple[Rect, str, str]],
               active: Sequence[Rect], width: float, height: float, *,
               allow_same_net_reuse: bool = False,
               allow_gate_in_active: bool = True) -> List[Point]:
    source, target = _point_key(start), _point_key(goal)
    if source == target:
        return [start]
    lo, hi = 6, round((width - 3.0) * 2)
    by, ty = 6, round((height - 3.0) * 2)
    def valid(key):
        if not (lo <= key[0] <= hi and by <= key[1] <= ty):
            return False
        return key in (source, target) or not _blocked_tin(
            _point_from_key(key), net, shapes, active,
            (_point_from_key(source),_point_from_key(target)),
            allow_same_net_reuse,allow_gate_in_active)
    if not valid(source) or not valid(target):
        raise GeometryError(f"TiN access for {net} lies outside legal cell routing bounds")
    queue: List[Tuple[float, int, Tuple[int, int]]] = [(0.0, 0, source)]
    previous: Dict[Tuple[int, int], Tuple[int, int] | None] = {source: None}
    distance = {source: 0.0}
    serial = 0
    while queue:
        _, _, current = heapq.heappop(queue)
        if current == target:
            path = []
            cursor: Tuple[int, int] | None = current
            while cursor is not None:
                path.append(_point_from_key(cursor))
                cursor = previous[cursor]
            return list(reversed(path))
        for dx, dy in TIN_ROUTE_DIRECTIONS:
            nxt = (current[0]+dx, current[1]+dy)
            if nxt in previous or not valid(nxt):
                continue
            distance[nxt] = distance[current]+0.5
            previous[nxt] = current
            serial += 1
            estimate = (abs(nxt[0]-target[0])+abs(nxt[1]-target[1]))/2
            heapq.heappush(queue, (distance[nxt]+estimate, serial, nxt))
    raise GeometryError(f"no legal TiN route for net {net} between {start} and {goal}")


def _wire_rect(a: Point, b: Point) -> Rect:
    if a[0] != b[0] and a[1] != b[1]:
        raise GeometryError("router emitted a non-Manhattan segment")
    half = ML1_WIDTH / 2
    return _rect(min(a[0], b[0]) - half, min(a[1], b[1]) - half,
                 max(a[0], b[0]) + half, max(a[1], b[1]) + half)


def _path_rectangles(path: Sequence[Point]) -> Iterable[Rect]:
    if len(path) < 2:
        return
    start = previous = path[0]
    old_direction = None
    for end in path[1:]:
        step = (end[0]-previous[0], end[1]-previous[1])
        direction = (0 if step[0] == 0 else (1 if step[0] > 0 else -1),
                     0 if step[1] == 0 else (1 if step[1] > 0 else -1))
        if old_direction is None:
            old_direction = direction
        elif direction != old_direction:
            yield _wire_rect(start, previous)
            start, old_direction = previous, direction
        previous = end
    yield _wire_rect(start, previous)

def _draw_island(layout, top, layers, devices, net_walk, x_start, y_start,
                 label_points, gate_contact_offset):
    """Draw one legal continuous DIFF row using native PCell gate/contact geometry."""
    if not devices or len(net_walk) != len(devices) + 1:
        raise GeometryError("island path has inconsistent transistor and diffusion-net counts")
    polarity = devices[0]["polarity"]
    if any(device["polarity"] != polarity for device in devices):
        raise GeometryError("one active island cannot mix N and P devices")
    w = max(device["w_um"] for device in devices)
    lengths = {device["l_um"] for device in devices}
    if len(lengths) != 1:
        raise GeometryError("mixed channel lengths in one active Euler row are unsupported")
    length = lengths.pop()
    gate_bay = max(4.0, (22.0 - length) / 2.0)  # native source/drain gate-bay contract
    pitch = CONTACT + length + 2 * gate_bay
    total_x = (len(devices) + 1) * CONTACT + len(devices) * (length + 2 * gate_bay)
    x0, y0 = _snap(x_start), _snap(y_start)
    active = _rect(x0, y0, x0 + total_x, y0 + w)
    _put_box(layout, top, layers[DIFF], active)
    implant_layer = layers[PAREA] if polarity == "p" else layers[NAREA]
    implant = _rect(active[0]-2, active[1]-2, active[2]+2, active[3]+2)
    _put_box(layout, top, implant_layer, implant)
    sd_y = _snap(y0 + w / 2)
    pads = []
    contacts = []
    diffusion_points = []
    tin_shapes = []

    for index, net in enumerate(net_walk):
        center = (_snap(x0 + CONTACT/2 + index*pitch), sd_y)
        cut = _rect(center[0]-CUT/2, center[1]-CUT/2, center[0]+CUT/2, center[1]+CUT/2)
        pad = _rect(center[0]-CONTACT/2, center[1]-CONTACT/2,
                    center[0]+CONTACT/2, center[1]+CONTACT/2)
        _put_box(layout, top, layers[CNT], cut)
        _put_box(layout, top, layers[ML1], pad)
        contacts.append({"kind": "diffusion", "net": net, "cut_um": list(cut), "landing_um": list(pad)})
        pads.append((pad, net, "pad"))
        diffusion_points.append((net, center))
    gates = []
    for index, device in enumerate(devices):
        gx = _snap(x0 + CONTACT + gate_bay + index*pitch)
        channel = _rect(gx, y0, gx + length, y0 + w)
        gate_y = _snap(y0+w+gate_contact_offset)
        gate_top = max(y0+w+6, gate_y-CONTACT/2+2.5)
        gate = _rect(gx-GATE_OVERLAP, y0-6, gx+length+GATE_OVERLAP, gate_top)
        tin_shapes.append((gate, device["g"], "gate"))

        _put_box(layout, top, layers[TIN], gate)
        if polarity == "n":
            _put_box(layout, top, layers[NWL], _rect(channel[0],channel[1],channel[2],channel[3])) if device.get("pwell") else None
        gate_center = (_snap(gx + length/2), gate_y)
        tin_landing = _rect(gate_center[0]-GATE_CONTACT/2, gate_center[1]-GATE_CONTACT/2,
                            gate_center[0]+GATE_CONTACT/2, gate_center[1]+GATE_CONTACT/2)
        cut = _rect(gate_center[0]-CUT/2, gate_center[1]-CUT/2,
                    gate_center[0]+CUT/2, gate_center[1]+CUT/2)
        _put_box(layout, top, layers[TIN], tin_landing)
        _put_box(layout, top, layers[CNT], cut)
        _put_box(layout, top, layers[ML1], tin_landing)
        tin_shapes.append((tin_landing, device["g"], "pad"))

        contacts.append({"kind": "gate", "net": device["g"], "cut_um": list(cut), "landing_um": list(tin_landing)})
        pads.append((tin_landing, device["g"], "pad"))
        gates.append({"device": device, "gate_center_um": list(gate_center),
                      "gate_bbox_um": list(channel), "tin_bbox_um": list(gate)})
        label_points[device["g"]].append(gate_center)
    return {"bbox_um": list(active), "active_bbox_um": active,
            "diffusion_points": diffusion_points,
            "gate_points": [(device["g"], tuple(gate["gate_center_um"]))
                            for device, gate in zip(devices, gates)],
            "pads": pads, "contacts": contacts, "gates": gates,
            "tin_shapes": tin_shapes,
            "tin_length_um": sum(max(r[2]-r[0],r[3]-r[1]) for r in
                                  [tuple(g["tin_bbox_um"]) for g in gates])}


def _rects_overlap(a: Rect, b: Rect) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _expanded(rect: Rect, margin: float) -> Rect:
    return _rect(rect[0]-margin,rect[1]-margin,rect[2]+margin,rect[3]+margin)


def _via_site_legal(center: Point, net: str,
                    m1_shapes: Sequence[Tuple[Rect,str,str]],
                    tin_shapes: Sequence[Tuple[Rect,str,str]],
                    active_regions: Sequence[Rect],
                    contacts: Sequence[Mapping[str,Any]],
                    width: float, height: float, *,
                    allow_same_net_overlap: bool = False) -> bool:
    pad=_rect(center[0]-VIA_CONTACT/2,center[1]-VIA_CONTACT/2,
              center[0]+VIA_CONTACT/2,center[1]+VIA_CONTACT/2)
    cut=_rect(center[0]-CUT/2,center[1]-CUT/2,
              center[0]+CUT/2,center[1]+CUT/2)
    if pad[0]<3.0 or pad[1]<3.0 or pad[2]>width-3.0 or pad[3]>height-3.0:
        return False
    if any(_rects_overlap(_expanded(pad,4.1),active)
           for active in active_regions):
        return False
    if any(_rects_overlap(_expanded(cut,4.1),tuple(record["cut_um"]))
           for record in contacts):
        return False
    # Same-net overlap is allowed only for deliberate via-to-wire landings.
    if any(_rects_overlap(_expanded(pad,4.1),rect) and
           not (allow_same_net_overlap and owner==net and
                _rects_overlap(pad,rect))
           for rect,owner,_kind in m1_shapes):
        return False
    if any(_rects_overlap(_expanded(pad,6.1),rect) and
           not (allow_same_net_overlap and owner==net and
                _rects_overlap(pad,rect))
           for rect,owner,_kind in tin_shapes):
        return False
    return True


def _draw_tin_ml1_via(layout,top,layers,center,net,m1_shapes,tin_shapes,contacts):
    pad=_rect(center[0]-VIA_CONTACT/2,center[1]-VIA_CONTACT/2,
              center[0]+VIA_CONTACT/2,center[1]+VIA_CONTACT/2)
    cut=_rect(center[0]-CUT/2,center[1]-CUT/2,
              center[0]+CUT/2,center[1]+CUT/2)
    _put_box(layout,top,layers[TIN],pad)
    _put_box(layout,top,layers[ML1],pad)
    _put_box(layout,top,layers[CNT],cut)
    tin_shapes.append((pad,net,"via"))
    m1_shapes.append((pad,net,"via"))
    contacts.append({"kind":"metal_via","net":net,"cut_um":list(cut),
                     "landing_um":list(pad),"conductor_pair":["TiN","ML1"]})


def _row_options(devices):
    options = []
    for reverse in (False, True):
        option = _euler_trail(devices, reverse)
        if option:
            ordered, walk = option
            if tuple(d["name"] for d in ordered) not in {tuple(x[0][i]["name"] for i in range(len(x[0]))) for x in options}:
                options.append((ordered, walk))
    return options

def _euler_islands(devices: Sequence[Mapping[str, Any]], reverse: bool = False):
    """Partition a MOS row into minimum-count continuous active trails."""
    adjacency: Dict[str, List[int]] = defaultdict(list)
    for index, device in enumerate(devices):
        adjacency[device["d"]].append(index)
        adjacency[device["s"]].append(index)
    remaining=set(range(len(devices)))
    components=[]
    while remaining:
        seed=min(remaining,key=lambda i:str(devices[i]["name"]))
        todo=[seed]; component=set()
        while todo:
            index=todo.pop()
            if index not in remaining: continue
            remaining.remove(index); component.add(index)
            device=devices[index]
            todo.extend(edge for net in (device["d"],device["s"])
                        for edge in adjacency[net] if edge in remaining)
        components.append([devices[i] for i in
                           sorted(component,key=lambda i:str(devices[i]["name"]))])
    components.sort(key=lambda group:min(str(device["name"]) for device in group))
    islands=[]
    for component in components:
        degree=defaultdict(int)
        for device in component:
            degree[device["d"]]+=1; degree[device["s"]]+=1
        odd=sorted(net for net,value in degree.items() if value%2)
        if len(odd)<=2:
            trail=_euler_trail(component,reverse)
            if trail:
                islands.append(trail)
                continue
        else:
            dummy_vertex="__STD_CELL_EULER_DUMMY__"
            component_names={str(device["name"]) for device in component}
            while dummy_vertex in degree: dummy_vertex="_"+dummy_vertex
            prefix="__STD_CELL_EULER_EDGE__"
            while any(name.startswith(prefix) for name in component_names):
                prefix="_"+prefix
            dummies=[{"name":f"{prefix}{index}","d":net,"s":dummy_vertex}
                     for index,net in enumerate(odd)]
            island_count=len(islands)
            trail=_euler_trail(component+dummies,reverse)
            if trail:
                ordered,walk=trail
                dummy_ids={id(device) for device in dummies}
                group=[]; nodes=[]
                for index,device in enumerate(ordered):
                    if id(device) in dummy_ids:
                        if group: islands.append((group,nodes))
                        group=[]; nodes=[]
                    else:
                        if not group: nodes=[walk[index]]
                        group.append(device); nodes.append(walk[index+1])
                if group: islands.append((group,nodes))
                if len(islands)>island_count: continue
        islands.extend(([device],[device["d"],device["s"]])
                       for device in component)
    def wire_cost(placement):
        net_x=defaultdict(list)
        position=15.0
        for group,nodes in placement:
            length=group[0]["l_um"]
            bay=max(4.0,(22.0-length)/2.0)
            pitch=CONTACT+length+2*bay
            for index,net in enumerate(nodes):
                net_x[net].append(_snap(position+CONTACT/2+index*pitch))
            for index,device in enumerate(group):
                net_x[device["g"]].append(
                    _snap(position+CONTACT+bay+length/2+index*pitch))
            position+=(len(group)+1)*CONTACT+len(group)*(length+2*bay)+5.0
        return sum(max(xs)-min(xs) for xs in net_x.values() if len(xs)>1)
    if len(islands)<=5:
        best_cost=float("inf"); best_key=None; best=None
        for order in permutations(range(len(islands))):
            for mask in range(1<<len(islands)):
                candidate=[]
                for position,index in enumerate(order):
                    group,nodes=islands[index]
                    if mask&(1<<position):
                        group=list(reversed(group)); nodes=list(reversed(nodes))
                    candidate.append((group,nodes))
                cost=wire_cost(candidate)
                key=tuple((tuple(d["name"] for d in group),tuple(nodes))
                          for group,nodes in candidate)
                if cost<best_cost-1e-9 or (abs(cost-best_cost)<=1e-9 and
                                           (best_key is None or key<best_key)):
                    best_cost,best_key,best=cost,key,candidate
        return best
    best=list(islands)
    while len(best)>1:
        best_cost=wire_cost(best)
        next_best=None; next_cost=best_cost
        for index in range(len(best)):
            group,nodes=best[index]
            candidate=list(best)
            candidate[index]=(list(reversed(group)),list(reversed(nodes)))
            cost=wire_cost(candidate)
            if cost<next_cost-1e-9:
                next_best,next_cost=candidate,cost
        for left in range(len(best)):
            for right in range(left+1,len(best)):
                candidate=list(best)
                candidate[left],candidate[right]=candidate[right],candidate[left]
                cost=wire_cost(candidate)
                if cost<next_cost-1e-9:
                    next_best,next_cost=candidate,cost
        if next_best is None: break
        best=next_best
    return best

def _island_arrangements(islands):
    if len(islands)>3:
        return [islands]
    options=[]
    for order in permutations(range(len(islands))):
        for mask in range(1<<len(islands)):
            placement=[]
            for position,index in enumerate(order):
                group,nodes=islands[index]
                if mask&(1<<position):
                    group=list(reversed(group)); nodes=list(reversed(nodes))
                placement.append((group,nodes))
            options.append(placement)
    return options


def _island_wire_cost(placements):
    net_x=defaultdict(list)
    for placement in placements:
        position=15.0
        for group,nodes in placement:
            length=group[0]["l_um"]
            bay=max(4.0,(22.0-length)/2.0)
            pitch=CONTACT+length+2*bay
            for index,net in enumerate(nodes):
                net_x[net].append(_snap(position+CONTACT/2+index*pitch))
            for index,device in enumerate(group):
                net_x[device["g"]].append(
                    _snap(position+CONTACT+bay+length/2+index*pitch))
            position+=(len(group)+1)*CONTACT+len(group)*(length+2*bay)+5.0
    return sum(max(xs)-min(xs) for xs in net_x.values() if len(xs)>1)


def build_cell(spec: Mapping[str, Any], height_um: float, *, fingers: int = 1,
               pin_order: Sequence[str] | None = None, mirror: bool = False,
               route_channel_um: float = 0.0):
    """Build canonical MOS geometry with optional explicit routing whitespace.

    Requested heights are never enlarged silently. A 5T/6T/7T request below the
    process/contact-derived minimum raises :class:`GeometryError` with the first
    feasible geometric height. Shared active is formed only along an explicit
    Euler path with electrically identical adjacent diffusion nets.
    """
    if not isinstance(spec, Mapping):
        raise GeometryError("cell specification must be a mapping")
    requested = _finite(height_um, "requested cell height")
    extension = _finite(route_channel_um, "route-channel width")
    if requested <= 0:
        raise GeometryError("requested cell height must be positive")
    if extension < 0:
        raise GeometryError("route-channel width must be nonnegative")
    try:
        nf = int(fingers)
    except (ValueError, TypeError) as exc:
        raise GeometryError("finger count must be a positive integer") from exc
    if nf < 1 or nf != fingers:
        raise GeometryError("finger count must be a positive integer")
    devices = _device_records(spec, nf)
    pins = list(spec.get("pins", []))
    if not pins:
        raise GeometryError("cell specification must name all logical pins")
    if len(set(pins)) != len(pins) or not {"VDD", "VSS"}.issubset(pins):
        raise GeometryError("cell pins must be unique and include VDD/VSS")
    ordered_pins = list(pin_order or pins)
    if set(ordered_pins) != set(pins) or len(ordered_pins) != len(pins):
        raise GeometryError("pin_order must be a permutation of the declared pins")
    known = set(pins)
    used = {net for d in devices for net in (d["d"], d["g"], d["s"])}
    references: Dict[str,int]=defaultdict(int)
    for device in devices:
        for terminal in ("d","g","s"):
            references[device[terminal]]+=1
    for net in used-known:
        if references[net]<2:
            raise GeometryError(f"internal MOS net {net} has fewer than two device-terminal references")

    if not set(pins).issubset(used):
        raise GeometryError("declared top-level pins absent from the MOS network: %s" % sorted(set(pins)-used))

    rows = {}
    for polarity in ("p", "n"):
        row_devices = [d for d in devices if d["polarity"] == polarity]
        if not row_devices:
            raise GeometryError(f"{spec.get('name')}: complementary {polarity} row is missing")
        options = _row_options(row_devices)
        if options:
            rows[polarity] = options
        else:
            # A non-Euler PUN/PDN remains legal as isolated, process-separated
            # islands; the external ML1 routing and resulting width are counted.
            rows[polarity] = [(sorted(row_devices, key=lambda d: d["name"]), None)]
    candidates = []
    for p_order, p_walk in rows["p"]:
        for n_order, n_walk in rows["n"]:
            if p_walk is None or n_walk is None:
                columns = [(i if i < len(p_order) else None,
                            i if i < len(n_order) else None)
                           for i in range(max(len(p_order),len(n_order)))]
            else:
                columns = _alignment(p_order, n_order)
            candidates.append((sum(p is not None and n is not None for p,n in columns),
                               tuple(d["name"] for d in p_order),
                               tuple(d["name"] for d in n_order),
                               p_order, p_walk, n_order, n_walk, columns))
    _, _, _, p_order, p_walk, n_order, n_walk, columns = max(candidates, key=lambda x:(x[0],x[1],x[2]))

    nw = max(d["w_um"] for d in n_order)
    pw = max(d["w_um"] for d in p_order)
    is_sequential = bool(spec.get("sequential"))
    n_y = 15.0
    # Sequential cells reserve a wider row gap for feedback-net escape routing.
    n_gate_contact_offset = (N_GATE_CONTACT_OFFSET if is_sequential
                             else COMPACT_GATE_CONTACT_OFFSET)
    p_gate_contact_offset = (P_GATE_CONTACT_OFFSET if is_sequential
                             else COMPACT_GATE_CONTACT_OFFSET)
    row_gap = 44.5 if is_sequential else 12.5
    row_floor = 86.5 if is_sequential else 56.0
    p_y = _snap(n_y + max(row_floor, nw + n_gate_contact_offset +
                           GATE_CONTACT/2 + row_gap))
    top_needed = p_y + pw + p_gate_contact_offset + GATE_CONTACT/2 + 4.0
    minimum_height = math.ceil(top_needed/10.0)*10.0
    actual_height = _snap(requested)
    if actual_height + 1e-9 < minimum_height:
        raise GeometryError(f"{spec.get('name')} height {requested:g}um is infeasible; contact/row geometry requires at least {minimum_height:g}um (requested {requested/10:g} nominal 10um tracks)")

    p_islands=_euler_islands(p_order) if p_walk is None else None
    n_islands=_euler_islands(n_order) if n_walk is None else None
    if p_islands is not None and n_islands is not None:
        best_cost=float("inf"); best_key=None; best_layout=None
        for p_candidate in _island_arrangements(p_islands):
            for n_candidate in _island_arrangements(n_islands):
                cost=_island_wire_cost((p_candidate,n_candidate))
                key=tuple((tuple(d["name"] for d in group),tuple(nodes))
                          for placement in (p_candidate,n_candidate)
                          for group,nodes in placement)
                if cost<best_cost-1e-9 or (abs(cost-best_cost)<=1e-9 and
                                           (best_key is None or key<best_key)):
                    best_cost,best_key,best_layout=cost,key,(p_candidate,n_candidate)
        p_islands,n_islands=best_layout

    layout = db.Layout(); layout.dbu = DBU
    name = str(spec.get("name", "CELL"))
    top = layout.create_cell(f"{name}_H{actual_height:g}_F{nf}{'_M' if mirror else ''}")
    layers = {number:_layer(layout,number) for number in (DIFF,TIN,CNT,ML1,TEXT,NWL,PAREA,NAREA)}
    pins_set=set(pins)
    terminals: Dict[str,List[Point]]=defaultdict(list)
    diff_terminals: Dict[str,List[Point]]=defaultdict(list)
    gate_terminals: Dict[str,List[Point]]=defaultdict(list)
    labels: Dict[str,List[Point]]=defaultdict(list)
    m1_shapes: List[Tuple[Rect,str,str]]=[]
    tin_shapes: List[Tuple[Rect,str,str]]=[]
    active_regions: List[Rect]=[]
    contact_records=[]; device_records=[]; row_records=[]
    total_tin=0.0
    # Preserve a legal left-edge escape lane for sequential CNT_TIN bridges.
    row_x_origin=60.0 if is_sequential else 15.0
    row_contact_span=CONTACT_SPAN

    def draw_row(order,walk,polarity,y,island_layout=None):
        nonlocal total_tin
        gate_contact_offset = (p_gate_contact_offset if polarity == "p"
                               else n_gate_contact_offset)
        island_origins={}
        if walk is None:
            runs=[]
            position=row_x_origin
            for island_index,(group,path_nodes) in enumerate(
                    _euler_islands(order) if island_layout is None else island_layout):
                length=group[0]["l_um"]
                gate_bay=max(4.0,(22.0-length)/2.0)
                active_width=(len(group)+1)*CONTACT+len(group)*(length+2*gate_bay)
                island_origins[island_index]=_snap(position)
                runs.append((island_index,island_index+1,group,path_nodes,len(group)>1))
                position+=active_width+5.0
        else:
            slot_by_device={}
            for slot,(p_index,n_index) in enumerate(columns):
                index=p_index if polarity=="p" else n_index
                if index is not None: slot_by_device[index]=slot
            runs=[]; start=0
            while start<len(order):
                end=start+1
                while end<len(order) and slot_by_device[end]==slot_by_device[end-1]+1:
                    end+=1
                runs.append((slot_by_device[start],slot_by_device[end-1]+1,
                             order[start:end],walk[start:end+1],True))
                start=end
        for first_slot,last_slot,group,path_nodes,shared in runs:
            if walk is None:
                x_start=island_origins[first_slot]
                first_device=group[0]
                bay=max(4.0,(22.0-first_device["l_um"])/2.0)
                x_gate=x_start+CONTACT+bay+first_device["l_um"]/2
            else:
                x_gate=row_x_origin+24+row_contact_span*first_slot
                x_start=x_gate-24
            row_right=x_start
            if shared or walk is None:
                island=_draw_island(layout,top,layers,group,path_nodes,x_start,y,
                                    labels,gate_contact_offset)
                for net,point in island["diffusion_points"]:
                    terminals[net].append(point)
                    diff_terminals[net].append(point)
                for net,point in island["gate_points"]:
                    terminals[net].append(point)
                    gate_terminals[net].append(point)
                m1_shapes.extend(island["pads"])
                tin_shapes.extend(island["tin_shapes"])
                active_regions.append(island["active_bbox_um"])
                contact_records.extend(island["contacts"])
                total_tin+=island["tin_length_um"]
                row_right=island["bbox_um"][2]
                row_bbox=[x_start,y-2,row_right,y+max(d["w_um"] for d in group)+2]
                for index,device in enumerate(group):
                    device_records.append({"name":device["name"],"polarity":polarity,
                        "d":device["d"],"g":device["g"],"s":device["s"],
                        "w_um":device["w_um"],"l_um":device["l_um"],
                        "active_row":"shared_euler_island" if len(group)>1 else "isolated_euler_island",
                        "gate_order_index":index,"bbox_um":row_bbox})
            else:
                for index,device in enumerate(group):
                    start_x=x_gate+row_contact_span*index-24
                    island=_draw_island(layout,top,layers,[dict(device)],
                                        [device["s"],device["d"]],start_x,y,labels,
                                        gate_contact_offset)
                    for net,point in island["diffusion_points"]:
                        terminals[net].append(point)
                        diff_terminals[net].append(point)
                    for net,point in island["gate_points"]:
                        terminals[net].append(point)
                        gate_terminals[net].append(point)
                    m1_shapes.extend(island["pads"])
                    tin_shapes.extend(island["tin_shapes"])
                    active_regions.append(island["active_bbox_um"])
                    contact_records.extend(island["contacts"])
                    total_tin+=island["tin_length_um"]
                    row_right=max(row_right,island["bbox_um"][2])
                    device_records.append({"name":device["name"],"polarity":polarity,
                        "d":device["d"],"g":device["g"],"s":device["s"],
                        "w_um":device["w_um"],"l_um":device["l_um"],
                        "active_row":"isolated_non_euler_island",
                        "bbox_um":[start_x,y-2,island["bbox_um"][2],y+device["w_um"]+2]})
                row_bbox=[x_start,y-2,row_right,y+max(d["w_um"] for d in group)+2]
            row_records.append({"polarity":polarity,"bbox_um":row_bbox,
                                "gate_order":[d["g"] for d in group],
                                "euler_shared_active":len(group)>1})

    draw_row(p_order,p_walk,"p",p_y,p_islands)
    draw_row(n_order,n_walk,"n",n_y,n_islands)

    cell_width=(max(r["bbox_um"][2] for r in row_records)+15.0+extension+
                (3.0 if is_sequential else 0.0))
    if mirror:
        def mirrored_point(point): return (_snap(cell_width-point[0]),point[1])
        def mirrored_rect(rect):
            return _rect(cell_width-rect[2],rect[1],cell_width-rect[0],rect[3])
        for layer_number in (DIFF,TIN,CNT,ML1,NWL,PAREA,NAREA):
            index=layers[layer_number]
            boxes=[(shape.box.left*DBU,shape.box.bottom*DBU,
                    shape.box.right*DBU,shape.box.top*DBU)
                   for shape in top.shapes(index).each()]
            top.shapes(index).clear()
            for rect in boxes:
                _put_box(layout,top,index,mirrored_rect(rect))
        terminals={net:[mirrored_point(p) for p in points] for net,points in terminals.items()}
        diff_terminals={net:[mirrored_point(p) for p in points] for net,points in diff_terminals.items()}
        gate_terminals={net:[mirrored_point(p) for p in points] for net,points in gate_terminals.items()}
        m1_shapes=[(mirrored_rect(r),net,kind) for r,net,kind in m1_shapes]
        tin_shapes=[(mirrored_rect(r),net,kind) for r,net,kind in tin_shapes]
        active_regions=[mirrored_rect(r) for r in active_regions]
        for record in contact_records:
            for key in ("cut_um","landing_um"):
                record[key]=mirrored_rect(record[key])
        device_records=[{**d,"bbox_um":mirrored_rect(d["bbox_um"])} for d in device_records]
        row_records=[{**r,"bbox_um":mirrored_rect(r["bbox_um"])} for r in row_records]

    # Gate nets stay on native TiN.  A gate CNT landing joins each TiN route
    # to its ML1 pin window without a Poly, VIA1, or synthetic interconnect.
    tin_route_shapes: List[Tuple[Rect,str,str]]=[]
    tin_route_length=0.0
    ml1_route_length=0.0
    gate_ml1=set()
    gate_via_points={}
    gate_escape_points={}
    gate_escape_paths={}
    gate_escape_via_points={}
    metal_via_count=0
    clock_inverse_nets=({item["output"] for item in
                         spec["sequential"]["clock_inversions"]}
                        if is_sequential else set())
    gate_order=sorted(gate_terminals.items(),
                      key=lambda item:(item[0] not in clock_inverse_nets,item[0]))
    for net,points in gate_order:
        unique=[]
        for point in points:
            if point not in unique: unique.append(point)
        if is_sequential and unique and (len(unique)>1 or net in diff_terminals):
            pending_paths=[]
            pending_shapes=[]
            escapes=[]
            escape_paths=[]
            try:
                for point in unique:
                    if not any(owner==net and kind=="gate" and
                               rect[0]<=point[0]<=rect[2] and
                               rect[3]<=point[1]
                               for rect,owner,kind in tin_shapes):
                        raise GeometryError(f"no TiN gate shape for {net} at {point}")
                    clearance=(GATE_CONTACT/2+TIN_WIDTH/2+TIN_SPACE+1.0)
                    route_shapes=tin_shapes+tin_route_shapes+pending_shapes
                    selected=None
                    for dx,dy in ((clearance,0.0),(-clearance,0.0),
                                  (0.0,clearance),(0.0,-clearance),
                                  (clearance,clearance),(-clearance,clearance),
                                  (clearance,-clearance),(-clearance,-clearance)):
                        candidate=(_snap(point[0]+dx),_snap(point[1]+dy))
                        access=(point,candidate)
                        if _blocked_tin(candidate,net,route_shapes,active_regions,
                                        access,False,False):
                            continue
                        try:
                            path=_route_tin(point,candidate,net,route_shapes,
                                active_regions,cell_width,actual_height,
                                allow_same_net_reuse=is_sequential,
                                allow_gate_in_active=False)
                        except GeometryError:
                            continue
                        selected=(candidate,path)
                        break
                    if selected is None:
                        raise GeometryError(f"no clear TiN escape for {net} at {point}")
                    escape,path=selected
                    escapes.append(escape)
                    escape_paths.append(path)
                    pending_paths.append(path)
                    pending_shapes.extend((rect,net,"wire")
                        for rect in _path_rectangles(path))
            except GeometryError:
                gate_ml1.add(net)
                continue
            if len(escapes)>1:
                try:
                    for escape in escapes[1:]:
                        path=_route_tin(escapes[0],escape,net,
                            tin_shapes+tin_route_shapes+pending_shapes,
                            active_regions,cell_width,actual_height,
                            allow_same_net_reuse=is_sequential,
                            allow_gate_in_active=False)
                        pending_paths.append(path)
                        pending_shapes.extend((rect,net,"wire")
                            for rect in _path_rectangles(path))
                except GeometryError:
                    gate_ml1.add(net)
                    if is_sequential:
                        gate_escape_points[net]=escapes
                        gate_escape_paths[net]=escape_paths
                    continue
            for path in pending_paths:
                for rectangle in _path_rectangles(path):
                    _put_box(layout,top,layers[TIN],rectangle)
                    tin_route_shapes.append((rectangle,net,"wire"))
                tin_route_length+=sum(abs(a[0]-b[0])+abs(a[1]-b[1])
                    for a,b in zip(path,path[1:]))
            gate_escape_points[net]=escapes
            continue

        if len(unique)<2:
            continue
        source=max(unique,key=lambda point:(point[1],-point[0]))
        hybrid=None
        # Dense sequential gate-only nets stay on TiN; bridge only mixed nets.
        if (not is_sequential or net in diff_terminals) and any(
               abs(point[0]-source[0])>1e-9
               for point in unique if point!=source):
            for dy in (17.0,-17.0,20.0,-20.0,24.0,-24.0,28.0,-28.0,32.0,-32.0):
                for dx in (0.0,-8.0,8.0,-17.0,17.0,-20.0,20.0):
                    via_point=(_snap(source[0]+dx),_snap(source[1]+dy))
                    if not _via_site_legal(via_point,net,m1_shapes,tin_shapes,
                                           active_regions,contact_records,
                                           cell_width,actual_height):
                        continue
                    try:
                        m1_path=_route_ml1(source,via_point,net,m1_shapes,
                                           cell_width,actual_height)
                    except GeometryError:
                        continue
                    via_pad=_rect(via_point[0]-VIA_CONTACT/2,
                                  via_point[1]-VIA_CONTACT/2,
                                  via_point[0]+VIA_CONTACT/2,
                                  via_point[1]+VIA_CONTACT/2)
                    escape_candidates=[]
                    keepout=(via_pad,f"__via_clearance_{net}","pad")
                    clearance=VIA_CONTACT/2+15.0
                    for escape in ((_snap(via_point[0]-clearance),via_point[1]),
                                   (_snap(via_point[0]+clearance),via_point[1])):
                        pending_paths=[[via_point,escape]]
                        pending_shapes=[(via_pad,net,"via"),keepout]
                        try:
                            for point in unique:
                                if point==source: continue
                                path=_route_tin(escape,point,net,
                                    tin_shapes+tin_route_shapes+pending_shapes,
                                    active_regions,cell_width,actual_height,
                                    allow_same_net_reuse=is_sequential,
                                    allow_gate_in_active=not is_sequential)
                                pending_paths.append(path)
                                pending_shapes.extend((rect,net,"wire")
                                    for rect in _path_rectangles(path))
                        except GeometryError:
                            continue
                        length=sum(abs(a[0]-b[0])+abs(a[1]-b[1])
                                   for path in pending_paths
                                   for a,b in zip(path,path[1:]))
                        escape_candidates.append((length,pending_paths))
                    if escape_candidates:
                        hybrid=(m1_path,via_point,
                                min(escape_candidates,key=lambda p:p[0])[1])
                        break
                if hybrid is not None: break
        if hybrid is None:
            root=unique[0]
            pending_paths=[]
            pending_shapes=[]
            try:
                for point in unique[1:]:
                    path=_route_tin(root,point,net,
                        tin_shapes+tin_route_shapes+pending_shapes,
                        active_regions,cell_width,actual_height,
                        allow_same_net_reuse=is_sequential,
                        allow_gate_in_active=not is_sequential)
                    pending_paths.append(path)
                    pending_shapes.extend((rect,net,"wire")
                                          for rect in _path_rectangles(path))
            except GeometryError:
                gate_ml1.add(net)
                continue
            for path in pending_paths:
                for rectangle in _path_rectangles(path):
                    _put_box(layout,top,layers[TIN],rectangle)
                    tin_route_shapes.append((rectangle,net,"wire"))
                tin_route_length+=sum(abs(a[0]-b[0])+abs(a[1]-b[1])
                                      for a,b in zip(path,path[1:]))
            continue
        m1_path,via_point,pending_paths=hybrid
        _draw_tin_ml1_via(layout,top,layers,via_point,net,m1_shapes,
                          tin_shapes,contact_records)
        metal_via_count+=1
        for rectangle in _path_rectangles(m1_path):
            _put_box(layout,top,layers[ML1],rectangle)
            m1_shapes.append((rectangle,net,"wire"))
        ml1_route_length+=sum(abs(a[0]-b[0])+abs(a[1]-b[1])
                              for a,b in zip(m1_path,m1_path[1:]))
        gate_via_points[net]=via_point
        for path in pending_paths:
            for rectangle in _path_rectangles(path):
                _put_box(layout,top,layers[TIN],rectangle)
                tin_route_shapes.append((rectangle,net,"wire"))
            tin_route_length+=sum(abs(a[0]-b[0])+abs(a[1]-b[1])
                                  for a,b in zip(path,path[1:]))

    diffusion_access_points={}
    bridge_nets=set()
    pre_routed_nets=set()
    bridge_dx_offsets=(0.0,-0.5,0.5,-1.0,1.0,-2.0,2.0,-4.0,4.0,
                       -8.0,8.0,-12.0,12.0)
    if is_sequential:
        bridge_dx_offsets+=(-16.0,16.0,-20.0,20.0,-24.0,24.0,
                            -28.0,28.0,-32.0,32.0,-36.0,36.0,-40.0,40.0)
    bridge_dy_offsets=(19,20,21,22,24,28,32,36,40,44,48.5,49,50,
                       -19,-20,-22,-24,-28,-32)
    bridge_candidates=tuple((dx,dy) for dy in bridge_dy_offsets
                            for dx in bridge_dx_offsets)
    active_left=min(rect[0] for rect in active_regions)
    active_right=max(rect[2] for rect in active_regions)
    def plan_ml1_tree(net,points,obstacles):
        unique=list(dict.fromkeys(points))
        if len(unique)<2: return []
        connected=[unique[0]]
        remaining=unique[1:]
        paths=[]
        while remaining:
            candidates=sorted(
                ((abs(source[0]-target[0])+abs(source[1]-target[1]),
                  source,target)
                 for source in connected for target in remaining),
                key=lambda candidate:candidate)
            best=None
            for _distance,source,target in candidates:
                try:
                    path=_route_ml1(source,target,net,obstacles,
                                    cell_width,actual_height)
                except GeometryError:
                    continue
                best=(source,target,path)
                break
            if best is None: return None
            source,target,path=best
            paths.append(path)
            connected.append(target)
            remaining.remove(target)
        return paths

    def commit_ml1_tree(net,paths):
        nonlocal ml1_route_length
        for path in paths:
            rectangles=list(_path_rectangles(path))
            for rectangle in rectangles:
                _put_box(layout,top,layers[ML1],rectangle)
                m1_shapes.append((rectangle,net,"wire"))
            ml1_route_length+=sum(abs(a[0]-b[0])+abs(a[1]-b[1])
                                  for a,b in zip(path,path[1:]))

    def commit_ml1_waypoint_tree(net,points):
        nonlocal ml1_route_length
        unique=list(dict.fromkeys(points))
        if len(unique)<2: return True
        bands=sorted({(rect[1],rect[3]) for rect in active_regions})
        y_tracks=[]
        for lower,upper in zip(bands,bands[1:]):
            if lower[1]<upper[0]:
                y_tracks.extend((_snap(lower[1]+9.0),
                                 _snap((lower[1]+upper[0])/2),
                                 _snap(upper[0]-9.0)))
        if not y_tracks: y_tracks=[_snap(actual_height/2)]
        top_track=_snap(actual_height-10.0)
        y_tracks.extend((top_track,_snap(10.0)))
        left_lanes=tuple(_snap(active_left-VIA_CONTACT/2-4.5-offset)
                         for offset in (0.0,12.0,24.0,36.0))
        right_lanes=tuple(_snap(active_right+VIA_CONTACT/2+24.5-offset)
                          for offset in (0.0,16.0))
        waypoints=sorted(
            {(x,y) for x in left_lanes+right_lanes for y in y_tracks},
            key=lambda waypoint:(
                0 if waypoint[1]==top_track and waypoint[0] in right_lanes else
                1 if waypoint[1]==top_track else 2,
                sum(abs(point[0]-waypoint[0])+abs(point[1]-waypoint[1])
                    for point in unique),waypoint))
        for waypoint in waypoints:
            paths=[]
            for point in unique:
                try:
                    path=_route_ml1(point,waypoint,net,m1_shapes,
                                    cell_width,actual_height)
                except GeometryError:
                    break
                paths.append(path)
            if len(paths)!=len(unique): continue
            rectangles=set()
            edges=set()
            for path in paths:
                rectangles.update(_path_rectangles(path))
                edges.update(tuple(sorted((a,b))) for a,b in
                             zip(path,path[1:]))
            for rectangle in sorted(rectangles):
                _put_box(layout,top,layers[ML1],rectangle)
                m1_shapes.append((rectangle,net,"wire"))
            ml1_route_length+=sum(abs(a[0]-b[0])+abs(a[1]-b[1])
                                  for a,b in edges)
            return True
        return False
    def commit_off_active_tin_tree(net,points):
        nonlocal metal_via_count,ml1_route_length,tin_route_length
        unique=sorted(set(points))
        if len(unique)<2: return True
        if len(unique)!=2: return False
        def commit_candidates(via_points):
            nonlocal metal_via_count,ml1_route_length,tin_route_length
            if any(not _via_site_legal(point,net,m1_shapes,
                    tin_shapes+tin_route_shapes,active_regions,contact_records,
                    cell_width,actual_height,allow_same_net_overlap=True)
                   for point in via_points):
                return False
            ml1_paths=[]
            try:
                for origin,via_point in zip(unique,via_points):
                    ml1_paths.append(_route_ml1(origin,via_point,net,
                                                m1_shapes,cell_width,
                                                actual_height))
            except GeometryError:
                return False
            via_pads=[(_rect(point[0]-VIA_CONTACT/2,
                             point[1]-VIA_CONTACT/2,
                             point[0]+VIA_CONTACT/2,
                             point[1]+VIA_CONTACT/2),net,"via")
                      for point in via_points]
            pending_tin=[]
            tin_paths=[]
            try:
                for point in via_points[1:]:
                    path=_route_tin(via_points[0],point,net,
                        tin_shapes+tin_route_shapes+pending_tin+via_pads,
                        active_regions,cell_width,actual_height,
                        allow_same_net_reuse=True,allow_gate_in_active=False)
                    tin_paths.append(path)
                    pending_tin.extend((rectangle,net,"wire")
                                       for rectangle in _path_rectangles(path))
            except GeometryError:
                return False
            for point,path in zip(via_points,ml1_paths):
                _draw_tin_ml1_via(layout,top,layers,point,net,m1_shapes,
                                  tin_shapes,contact_records)
                metal_via_count+=1
                for rectangle in _path_rectangles(path):
                    _put_box(layout,top,layers[ML1],rectangle)
                    m1_shapes.append((rectangle,net,"wire"))
                ml1_route_length+=sum(abs(a[0]-b[0])+abs(a[1]-b[1])
                                      for a,b in zip(path,path[1:]))
            for path in tin_paths:
                for rectangle in _path_rectangles(path):
                    _put_box(layout,top,layers[TIN],rectangle)
                    tin_route_shapes.append((rectangle,net,"wire"))
                tin_route_length+=sum(abs(a[0]-b[0])+abs(a[1]-b[1])
                                      for a,b in zip(path,path[1:]))
            return True
        bands=sorted({(rect[1],rect[3]) for rect in active_regions})
        gap_tracks=[_snap((lower[1]+upper[0])/2)
                    for lower,upper in zip(bands,bands[1:])
                    if lower[1]<upper[0]]
        left_lanes=tuple(_snap(active_left-VIA_CONTACT/2-4.5-offset)
                         for offset in (0.0,12.0,24.0,36.0))
        right_lanes=tuple(_snap(active_right+VIA_CONTACT/2+24.5-offset)
                          for offset in (0.0,16.0))
        top_y=_snap(actual_height-10.0)
        top_sites=[]
        for origin in unique:
            candidates={(_snap(origin[0]+dx),top_y)
                        for dx in range(-20,21,2)}
            legal=[point for point in candidates
                   if _via_site_legal(point,net,m1_shapes,
                       tin_shapes+tin_route_shapes,active_regions,
                       contact_records,cell_width,actual_height,
                       allow_same_net_overlap=True)]
            top_sites.append(sorted(legal,key=lambda point:(
                abs(point[0]-origin[0]),point))[:8])
        top_layouts=[]
        if all(top_sites):
            for first in top_sites[0]:
                for second in top_sites[1]:
                    layout=(first,second)
                    cost=sum(abs(origin[0]-site[0])+abs(origin[1]-site[1])
                             for origin,site in zip(unique,layout))
                    top_layouts.append((cost,layout))
        for _cost,layout in sorted(top_layouts)[:32]:
            if commit_candidates(layout): return True
        def nearby_sites(origin):
            candidates={(_snap(origin[0]+dx),_snap(origin[1]+dy))
                        for dy in (18.0,20.0,22.0,24.0,26.0,28.0,
                                   30.0,32.0,34.0,-18.0,-20.0,-22.0,
                                   -24.0,-26.0,-28.0,-30.0,-32.0,-34.0)
                        for dx in range(-16,17,2)}
            legal=[point for point in candidates
                   if _via_site_legal(point,net,m1_shapes,
                       tin_shapes+tin_route_shapes,active_regions,
                       contact_records,cell_width,actual_height,
                       allow_same_net_overlap=True)]
            return sorted(legal,key=lambda point:(
                abs(point[0]-origin[0])+abs(point[1]-origin[1]),
                abs(point[1]-origin[1]),point))[:8]
        near_sites=[nearby_sites(point) for point in unique]
        layouts=[]
        if all(near_sites):
            for first in near_sites[0]:
                for second in near_sites[1]:
                    if abs(first[0]-second[0])+abs(first[1]-second[1])<12.0:
                        continue
                    layout=(first,second)
                    cost=sum(abs(origin[0]-site[0])+abs(origin[1]-site[1])
                             for origin,site in zip(unique,layout))
                    layouts.append((cost,layout))
        for _cost,layout in sorted(layouts)[:32]:
            if commit_candidates(layout): return True
        layouts=[((left_x,y),(right_x,y))
                 for y in gap_tracks
                 for left_x in left_lanes for right_x in right_lanes]
        layouts.sort(key=lambda layout:(
            sum(abs(origin[0]-site[0])+abs(origin[1]-site[1])
                for origin,site in zip(unique,layout)),layout))
        for via_points in layouts:
            if commit_candidates(via_points): return True
        for dy in (22.0,-22.0,24.0,-24.0,26.0,-26.0,
                   28.0,-28.0,30.0,-30.0,32.0,-32.0):
            via_points=[(_snap(point[0]),_snap(point[1]+dy))
                        for point in unique]
            if commit_candidates(via_points): return True
        return False

    bridgeable_nets=(set(gate_terminals)&set(diff_terminals))-gate_ml1
    if is_sequential:
        bridgeable_nets.update((set(gate_terminals)&set(diff_terminals))&
                               gate_ml1)
        bridgeable_nets.update(set(diff_terminals)-set(gate_terminals)-{"VDD","VSS"})
        bridgeable_nets.update((set(gate_terminals)-set(diff_terminals))&
                               gate_ml1)
        bridgeable_nets.update(
            {spec.get("sequential",{}).get("data")}&set(gate_terminals))
    outputs=set(spec.get("outputs", []))
    data_net=(spec.get("sequential",{}).get("data")
              if is_sequential else None)
    if (is_sequential and data_net in diff_terminals and
            data_net not in gate_terminals):
        planned=plan_ml1_tree(data_net,diff_terminals[data_net],m1_shapes)
        if planned is None:
            raise GeometryError(
                f"no legal ML1 data tree for sequential net {data_net}")
        commit_ml1_tree(data_net,planned)
        pre_routed_nets.add(data_net)
        bridgeable_nets.discard(data_net)
    if is_sequential and "VDD" not in pre_routed_nets:
        supply_points=list(dict.fromkeys(diff_terminals.get("VDD",())))
        if len(supply_points)>1:
            if not commit_off_active_tin_tree("VDD",supply_points):
                if not commit_ml1_waypoint_tree("VDD",supply_points):
                    planned=plan_ml1_tree("VDD",supply_points,m1_shapes)
                    if planned is None:
                        raise GeometryError(
                            "no legal ML1 VDD tree through available routing channels")
                    commit_ml1_tree("VDD",planned)
            pre_routed_nets.add("VDD")
    early_gate_escape_net=None
    if is_sequential:
        gate_escape_order=sorted(
            (net for net in bridgeable_nets
             if net in gate_escape_paths and net not in clock_inverse_nets),
            key=lambda net:(min((point[0] for point in
                diff_terminals.get(net,())),
                default=min((point[0] for point in
                    gate_terminals.get(net,())),default=float("inf"))),net))
        if gate_escape_order:
            early_gate_escape_net=gate_escape_order[0]
    if is_sequential:
        bridge_order=sorted(bridgeable_nets,key=lambda net:(
            0 if net in outputs else
            1 if net==early_gate_escape_net else
            2 if net in clock_inverse_nets else
            3 if net==data_net else
            4 if net in gate_escape_paths and
                net not in clock_inverse_nets else
            5 if net in gate_escape_paths else
            6 if net in gate_terminals and net in diff_terminals and
                net not in clock_inverse_nets else
            7 if net in gate_terminals and net in diff_terminals else
            8 if net in gate_terminals else 9,
            min((point[0] for point in diff_terminals.get(net,())),
                default=min((point[0] for point in
                             gate_terminals.get(net,())),
                            default=float("inf"))),net))
    else:
        bridge_order=sorted(bridgeable_nets)
    for net in bridge_order:
        edge_clearance=20.0 if is_sequential else 0.0
        left_lane_offsets=((0.0,12.0,24.0,36.0)
                           if is_sequential else (0.0,))
        left_edge_xs=tuple(_snap(active_left-VIA_CONTACT/2-4.5-offset)
                           for offset in left_lane_offsets)
        right_lane_offsets=(0.0,16.0) if is_sequential else (0.0,)
        right_edge_xs=tuple(_snap(active_right+VIA_CONTACT/2+4.5+
                                  edge_clearance-offset)
                            for offset in right_lane_offsets)
        bridge_edge_xs=left_edge_xs+right_edge_xs
        gate_points=list(dict.fromkeys(gate_terminals.get(net,())))
        diffusion_points=list(dict.fromkeys(diff_terminals[net]))
        if (not diffusion_points and
                not (is_sequential and
                     (net in gate_escape_paths or net in gate_ml1 or
                      net==data_net))):
            continue
        if not gate_points and len(diffusion_points)<2:
            continue
        local_m1=list(m1_shapes)
        local_tin=list(tin_shapes)+list(tin_route_shapes)
        local_contacts=list(contact_records)
        access_paths=[]; via_points=[]
        edge_points=diffusion_points+gate_points
        via_diffusion_points=diffusion_points
        if is_sequential and gate_points and len(diffusion_points)>1:
            planned_diffusion_tree=plan_ml1_tree(
                net,diffusion_points,m1_shapes)
            if planned_diffusion_tree is not None:
                commit_ml1_tree(net,planned_diffusion_tree)
                via_diffusion_points=diffusion_points[:1]
        if is_sequential and net in clock_inverse_nets:
            shared_edge_xs=tuple(reversed(left_edge_xs))+right_edge_xs
        elif is_sequential and net==early_gate_escape_net:
            shared_edge_xs=tuple(reversed(right_edge_xs))+left_edge_xs
        elif is_sequential and gate_points:
            shared_edge_xs=left_edge_xs+right_edge_xs
        elif is_sequential and net in outputs:
            shared_edge_xs=right_edge_xs+left_edge_xs
        else:
            shared_edge_xs=sorted(bridge_edge_xs,
                key=lambda x:sum(abs(x-point[0]) for point in edge_points))
        selected_edge_x=None
        net_bridge_candidates=bridge_candidates
        if is_sequential and net in clock_inverse_nets:
            net_bridge_candidates=tuple(sorted(bridge_candidates,
                key=lambda offset:(
                    not 12.0<=offset[0]<=32.0,
                    abs(offset[0]-20.0)+abs(abs(offset[1])-24.0),
                    -offset[0],abs(offset[1]))))
        for point_index,point in enumerate(via_diffusion_points):
            edge_xs=(shared_edge_xs if selected_edge_x is None or
                     (is_sequential and net not in outputs)
                     else (selected_edge_x,))
            boundary_dy_offsets=bridge_dy_offsets
            if is_sequential:
                center_margin=3.0+VIA_CONTACT/2
                boundary_dy_offsets=tuple(sorted(bridge_dy_offsets,
                    key=lambda dy:(
                        not center_margin<=point[1]+dy<=actual_height-center_margin,
                        min(point[1]+dy,actual_height-(point[1]+dy)),
                        abs(dy))))
            boundary_candidates=tuple((x-point[0],dy)
                for x in edge_xs for dy in boundary_dy_offsets)
            if is_sequential and net in clock_inverse_nets:
                candidate_offsets=net_bridge_candidates+boundary_candidates
            elif is_sequential and not gate_points and point_index==0:
                candidate_offsets=(
                    boundary_candidates+net_bridge_candidates
                    if net in outputs else
                    net_bridge_candidates+boundary_candidates)
            elif is_sequential:
                candidate_offsets=boundary_candidates+(
                    net_bridge_candidates if selected_edge_x is None or
                    net not in outputs else ())
            else:
                candidate_offsets=net_bridge_candidates
            chosen=None
            for dx,dy in candidate_offsets:
                candidate=(_snap(point[0]+dx),_snap(point[1]+dy))
                if not _via_site_legal(candidate,net,local_m1,local_tin,
                                       active_regions,local_contacts,
                                       cell_width,actual_height):
                    continue
                try:
                    path=_route_ml1(point,candidate,net,local_m1,
                                    cell_width,actual_height)
                except GeometryError:
                    continue
                pad=_rect(candidate[0]-VIA_CONTACT/2,candidate[1]-VIA_CONTACT/2,
                          candidate[0]+VIA_CONTACT/2,candidate[1]+VIA_CONTACT/2)
                cut=_rect(candidate[0]-CUT/2,candidate[1]-CUT/2,
                          candidate[0]+CUT/2,candidate[1]+CUT/2)
                local_m1.append((pad,net,"via"))
                local_tin.append((pad,net,"via"))
                local_contacts.append({"kind":"metal_via","net":net,
                    "cut_um":list(cut),"landing_um":list(pad),
                    "conductor_pair":["TiN","ML1"]})
                local_m1.extend((rect,net,"wire") for rect in _path_rectangles(path))
                access_paths.append(path); via_points.append(candidate)
                chosen=candidate
                if is_sequential and candidate[0] in bridge_edge_xs:
                    selected_edge_x=candidate[0]
                break
            if chosen is None:
                access_paths=[]; via_points=[]
                break
        if len(via_points)!=len(via_diffusion_points): continue
        pending_tin=[]; tin_paths=[]; tin_failed=False
        via_pads=[(_rect(point[0]-VIA_CONTACT/2,point[1]-VIA_CONTACT/2,
                         point[0]+VIA_CONTACT/2,point[1]+VIA_CONTACT/2),
                   net,"via") for point in via_points]
        gate_roots=([gate_via_points[net]] if net in gate_via_points else
                    list(dict.fromkeys(gate_escape_points.get(net,gate_points))))
        if (is_sequential and not diffusion_points and
                (net in gate_ml1 or net==data_net)):
            tin_failed=True
        if net in gate_escape_paths:
            tin_paths.extend(gate_escape_paths[net])
            for path in gate_escape_paths[net]:
                pending_tin.extend((rect,net,"wire")
                                   for rect in _path_rectangles(path))
            for point in via_points[1:]:
                try:
                    path=_route_tin(via_points[0],point,net,
                        tin_shapes+tin_route_shapes+pending_tin+via_pads,
                        active_regions,cell_width,actual_height,
                        allow_same_net_reuse=True,allow_gate_in_active=False)
                except GeometryError:
                    tin_failed=True
                    break
                pending_tin.extend((rect,net,"wire")
                                   for rect in _path_rectangles(path))
            if not tin_failed:
                for root in gate_roots:
                    if root in via_points: continue
                    selected=None
                    for point in via_points:
                        try:
                            selected=_route_tin(root,point,net,
                                tin_shapes+tin_route_shapes+pending_tin+via_pads,
                                active_regions,cell_width,actual_height,
                                allow_same_net_reuse=True,
                                allow_gate_in_active=False)
                        except GeometryError:
                            continue
                        break
                    if selected is None:
                        tin_failed=True
                        break
                    tin_paths.append(selected)
                    pending_tin.extend((rect,net,"wire")
                                       for rect in _path_rectangles(selected))
        else:
            if gate_points:
                tin_roots=list(dict.fromkeys(gate_roots))
            else:
                tin_roots=[via_points[0]]
            for point in via_points:
                if point in tin_roots: continue
                selected=None
                for root in sorted(tin_roots,
                                   key=lambda origin:abs(origin[0]-point[0])+abs(origin[1]-point[1])):
                    try:
                        path=_route_tin(root,point,net,
                            tin_shapes+tin_route_shapes+pending_tin,
                            active_regions,cell_width,actual_height,
                            allow_same_net_reuse=is_sequential,
                            allow_gate_in_active=not is_sequential)
                    except GeometryError:
                        continue
                    selected=path
                    break
                if selected is None:
                    tin_failed=True
                    break
                tin_paths.append(selected)
                pending_tin.extend((rect,net,"wire")
                                   for rect in _path_rectangles(selected))
        fallback_gate_vias=[]
        fallback_ml1_paths=[]
        gate_fallback=False
        if tin_failed and is_sequential and gate_points:
            local_offsets=[
                (dx/2,dy/2) for dx in range(-48,49)
                for dy in range(-48,49) if dx or dy]
            local_offsets.sort(key=lambda offset:(
                abs(offset[0])+abs(offset[1]),offset[1],offset[0]))
            distance=24.5
            while distance<=max(cell_width,actual_height):
                local_offsets.extend(((0.0,distance),(distance,0.0),
                    (-distance,0.0),(0.0,-distance),(distance,distance),
                    (-distance,distance),(distance,-distance),
                    (-distance,-distance)))
                distance+=0.5
            local_wire_candidates={
                (_snap((rect[0]+rect[2])/2),_snap((rect[1]+rect[3])/2))
                for rect,owner,kind in local_m1
                if owner==net and kind=="wire"}
            local_tin=list(tin_shapes)+list(tin_route_shapes)+list(pending_tin)+via_pads
            fallback_failed=False
            fallback_roots=gate_roots
            if net not in gate_escape_paths and diffusion_points:
                fallback_roots=sorted(gate_roots,
                    key=lambda root:sum(abs(root[0]-point[0])+
                                        abs(root[1]-point[1])
                                        for point in via_points))[:1]
            for root in fallback_roots:
                if root in via_points:
                    continue
                selected=None
                wire_candidates=sorted(local_wire_candidates,
                    key=lambda point:abs(point[0]-root[0])+abs(point[1]-root[1]))
                candidates=wire_candidates+[
                    (_snap(root[0]+dx),_snap(root[1]+dy))
                    for dx,dy in local_offsets]
                for candidate in dict.fromkeys(candidates):
                    if not _via_site_legal(candidate,net,local_m1,local_tin,
                                           active_regions,local_contacts,
                                           cell_width,actual_height,
                                           allow_same_net_overlap=True):
                        continue
                    pad=_rect(candidate[0]-VIA_CONTACT/2,
                              candidate[1]-VIA_CONTACT/2,
                              candidate[0]+VIA_CONTACT/2,
                              candidate[1]+VIA_CONTACT/2)
                    try:
                        path=_route_tin(root,candidate,net,
                            local_tin+[(pad,net,"via")],active_regions,
                            cell_width,actual_height,allow_same_net_reuse=True,
                            allow_gate_in_active=False)
                    except GeometryError:
                        continue
                    connected_points=via_points+diffusion_points+[
                        point for point,_ in fallback_gate_vias]
                    candidate_m1=local_m1+[(pad,net,"via")]
                    connection_path=None
                    for point in connected_points:
                        try:
                            connection_path=_route_ml1(candidate,point,net,
                                candidate_m1,cell_width,actual_height)
                            break
                        except GeometryError:
                            continue
                    if connected_points and connection_path is None:
                        continue
                    selected=(candidate,path,pad,connection_path)
                    break
                if selected is None:
                    fallback_failed=True
                    break
                candidate,path,pad,connection_path=selected
                cut=_rect(candidate[0]-CUT/2,candidate[1]-CUT/2,
                          candidate[0]+CUT/2,candidate[1]+CUT/2)
                fallback_gate_vias.append((candidate,path))
                tin_paths.append(path)
                pending_tin.extend((rect,net,"wire")
                                   for rect in _path_rectangles(path))
                if connection_path is not None:
                    fallback_ml1_paths.append(connection_path)
                local_m1.append((pad,net,"via"))
                local_tin.append((pad,net,"via"))
                local_contacts.append({"kind":"metal_via","net":net,
                    "cut_um":list(cut),"landing_um":list(pad),
                    "conductor_pair":["TiN","ML1"]})
            if not fallback_failed:
                tin_failed=False
                gate_fallback=True

        for point,path in zip(via_points,access_paths):
            _draw_tin_ml1_via(layout,top,layers,point,net,m1_shapes,
                              tin_shapes,contact_records)
            metal_via_count+=1
            for rectangle in _path_rectangles(path):
                _put_box(layout,top,layers[ML1],rectangle)
                m1_shapes.append((rectangle,net,"wire"))
            ml1_route_length+=sum(abs(a[0]-b[0])+abs(a[1]-b[1])
                                  for a,b in zip(path,path[1:]))
        if gate_fallback:
            for point,_path in fallback_gate_vias:
                _draw_tin_ml1_via(layout,top,layers,point,net,m1_shapes,
                                  tin_shapes,contact_records)
                metal_via_count+=1
            for path in fallback_ml1_paths:
                for rectangle in _path_rectangles(path):
                    _put_box(layout,top,layers[ML1],rectangle)
                    m1_shapes.append((rectangle,net,"wire"))
                ml1_route_length+=sum(abs(a[0]-b[0])+abs(a[1]-b[1])
                                      for a,b in zip(path,path[1:]))
            for path in tin_paths:
                for rectangle in _path_rectangles(path):
                    _put_box(layout,top,layers[TIN],rectangle)
                    tin_route_shapes.append((rectangle,net,"wire"))
                tin_route_length+=sum(abs(a[0]-b[0])+abs(a[1]-b[1])
                                      for a,b in zip(path,path[1:]))
            diffusion_access_points[net]=list(via_points)
            gate_escape_via_points[net]=[point for point,_path in fallback_gate_vias]
            if is_sequential and gate_points:
                pre_routed_nets.add(net)
            else:
                planned=plan_ml1_tree(net,via_points+diffusion_points+
                    [point for point,_path in fallback_gate_vias],m1_shapes)
                if planned is not None:
                    commit_ml1_tree(net,planned)
                    pre_routed_nets.add(net)
                else:
                    diffusion_access_points.pop(net,None)
            gate_ml1.discard(net)
            continue
        if tin_failed:
            diffusion_access_points[net]=list(via_points)
            if is_sequential and gate_points and len(via_points)<len(diffusion_points):
                diffusion_access_points.pop(net,None)
            if is_sequential and not gate_points:
                planned=plan_ml1_tree(net,via_points,m1_shapes)
                if planned is not None:
                    commit_ml1_tree(net,planned)
                    pre_routed_nets.add(net)
        else:
            for path in tin_paths:
                for rectangle in _path_rectangles(path):
                    _put_box(layout,top,layers[TIN],rectangle)
                    tin_route_shapes.append((rectangle,net,"wire"))
                tin_route_length+=sum(abs(a[0]-b[0])+abs(a[1]-b[1])
                                      for a,b in zip(path,path[1:]))
            if is_sequential and gate_points:
                pre_routed_nets.add(net)
            bridge_nets.add(net)
            gate_ml1.discard(net)

    # Diffusion-only nets use ML1; mixed gate/diffusion nets use validated
    # off-active CNT_TIN bridges when a complete bridge can be routed.
    route_points={net:list(diffusion_access_points.get(net,points))
                  for net,points in diff_terminals.items()
                  if net not in bridge_nets and net not in pre_routed_nets}
    for net,points in gate_terminals.items():
        if net in bridge_nets or net in pre_routed_nets: continue
        if net in gate_ml1:
            route_points.setdefault(net,[]).extend(points)
        elif net in gate_escape_via_points:
            route_points.setdefault(net,[]).extend(gate_escape_via_points[net])
            if net in gate_via_points:
                route_points[net].append(gate_via_points[net])
        elif net in gate_via_points:
            route_points.setdefault(net,[]).append(gate_via_points[net])
        elif points:
            route_points.setdefault(net,[]).append(points[0])
    items=list(route_points.items())
    def route_span(item):
        points=list(dict.fromkeys(item[1]))
        return max((abs(a[0]-b[0])+abs(a[1]-b[1])
                    for index,a in enumerate(points) for b in points[index+1:]),
                   default=0.0)
    def route_priority(net):
        return (-1 if is_sequential and net in {"VDD","VSS"} else
                0 if net not in pins_set else
                1 if net in outputs else
                2 if net in gate_ml1 else
                4 if net in {"VDD","VSS"} else 3)
    orderings=[
        sorted(items,key=lambda item:(route_priority(item[0]),item[0])),
        sorted(items,key=lambda item:(0 if item[0] in outputs else
                                      1 if item[0] not in pins_set else
                                      2 if item[0] in gate_ml1 else
                                      4 if item[0] in {"VDD","VSS"} else 3,
                                      item[0])),
        sorted(items,key=lambda item:(-route_span(item),route_priority(item[0]),item[0])),
        sorted(items,key=lambda item:(-route_priority(item[0]),item[0])),
    ]
    priority_order=orderings[1]
    seen_orders={tuple(net for net,_points in order) for order in orderings}
    signal_items=[item for item in priority_order if item[0] not in {"VDD","VSS"}]
    def add_route_order(order):
        key=tuple(net for net,_points in order)
        if key not in seen_orders:
            seen_orders.add(key)
            orderings.append(order)
    for first in signal_items:
        for second in signal_items:
            if first[0]==second[0]: continue
            add_route_order([first,second]+[item for item in priority_order
                                            if item[0] not in {first[0],second[0]}])
    planned_routes=None
    last_route_error=None
    selected_route_order=None
    for order_index,route_order in enumerate(orderings):
        for reverse_roots in (False,True):
            obstacles=list(m1_shapes)
            candidate=[]
            try:
                for net,points in route_order:
                    unique=list(dict.fromkeys(points))
                    if len(unique)<2: continue
                    root=unique[-1] if reverse_roots else unique[0]
                    targets=list(reversed(unique[:-1])) if reverse_roots else unique[1:]
                    for point in targets:
                        path=_route_ml1(root,point,net,obstacles,cell_width,actual_height)
                        rectangles=list(_path_rectangles(path))
                        candidate.append((net,path,rectangles))
                        obstacles.extend((rectangle,net,"wire") for rectangle in rectangles)
                planned_routes=candidate
                selected_route_order={"variant":order_index,
                                      "reverse_roots":reverse_roots}
                break
            except GeometryError as error:
                last_route_error=error
        if planned_routes is not None: break
    if planned_routes is None:
        raise last_route_error or GeometryError("no legal ML1 routing order")
    for net,path,rectangles in planned_routes:
        for rectangle in rectangles:
            _put_box(layout,top,layers[ML1],rectangle)
            m1_shapes.append((rectangle,net,"wire"))
        ml1_route_length+=sum(abs(a[0]-b[0])+abs(a[1]-b[1])
                              for a,b in zip(path,path[1:]))
    text_layer=layers[TEXT]
    pin_windows={}
    for pin in ordered_pins:
        unique=[]
        for point in terminals[pin]:
            if point not in unique: unique.append(point)
        windows=[]
        for point in unique:
            pad=next((rect for rect,owner,kind in m1_shapes
                      if owner==pin and kind=="pad" and
                      abs((rect[0]+rect[2])/2-point[0])<1e-9 and
                      abs((rect[1]+rect[3])/2-point[1])<1e-9),None)
            if pad is None:
                raise GeometryError(f"top-level pin {pin} has no ML1 contact window at {point}")
            windows.append(list(pad))
        _put_text(layout,top,text_layer,pin,unique[0])
        pin_windows[pin]=windows
    bbox=[0.0,0.0,_snap(cell_width),actual_height]
    manifest={"name":name,"process_profile":"icps2023_5_1m","bbox_um":bbox,
        "width_um":bbox[2],"height_um":bbox[3],"area_um2":bbox[2]*bbox[3],
        "pins":pin_windows,"devices":device_records,"rows":row_records,
        "mos_count":len(device_records),"contacts_count":len(contact_records),
        "contacts":contact_records,"tin_length_um":_snap(total_tin),
        "nominal_track_count_assumption":actual_height/10.0,
        "track_pitch_assumption_um":10.0,
        "track_pitch_status":"derived_lower_bound_from_6um_ML1_width+4um_spacing; not a qualified routing pitch",
        "requested_height_um":requested,"actual_height_um":actual_height,
        "route_channel_um":extension,
        "folding":{"requested_parallel_fingers":nf,"physical_parallel_devices":len(devices),
                   "shared_active_euler_rows":sum(r["euler_shared_active"] for r in row_records)},
        "variant":{"fingers":nf,"mirror":bool(mirror),"pin_order":ordered_pins},
        "routing":{"local_conductors":["ML1","TiN"],"unrouted_nets":[],
                    "ML1_wire_length_um":_snap(ml1_route_length),
                    "TiN_wire_length_um":_snap(tin_route_length),
                    "gate_nets_fallback_to_ML1":sorted(gate_ml1),
                    "additional_CNT_vias":metal_via_count,
                    "route_order_variant":selected_route_order,
                    "direct_tin_diff_contact":False},
        "provenance":{"device_geometry":"native ICPS2023_5 PCell equation: contact=13um, gate L>=6um, gate bay=max(4,(22-L)/2), gate overlap2um, CNT5um; gate-contact offset=26um compact, N/P=33/35um sequential",
                       "sheet_resistance_ohm_sq":{"ML1":0.2,"TIN":20.0},
                       "qualification":"canonical DRC/LVS/PEX must still pass; no signoff claim"}}
    return layout,top,manifest


__all__=["GeometryError","build_cell"]

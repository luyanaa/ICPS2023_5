# Constraints for the combinational LibreLane designs.
#
# Both checked-in designs are pure combinational logic and the experimental
# Liberty has no sequential arcs, so timing is expressed against a virtual
# clock instead of a real clock port.  The 100 ns period is not a target: it
# only bounds the input->output paths so STA has a defined scale, and the 2 ns
# input delay mirrors the 2 ns input slew of the characterized 5 fF arc point.
create_clock -name virtual_clock -period 100
set_clock_uncertainty 0 [get_clocks virtual_clock]
set_input_delay -clock virtual_clock 2.0 [all_inputs]
set_output_delay -clock virtual_clock 2.0 [all_outputs]
# capacitive_load_unit in the Liberty is 1 ff, so 0.005 pF is the characterized
# 5 fF output load.
set_load 0.005 [all_outputs]

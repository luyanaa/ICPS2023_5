* ICPS2023_5 BSIMSOI v4.4 (ngspice LEVEL=58), stage-1 PDF fit.
* Primary data: PDK_minimalFabSOICMOS.pdf p.7 and digitized p.10-11 curves;
* raw graph samples and fit plot are in ../fit/.
* This is a compact-model fit to the published PDK measurements, not silicon
* calibration. Only VTH0, U0, NFACTOR, K1, and RDSW were fitted; all unlisted
* B4SOI parameters retain ngspice defaults. Body/back-gate, CV, temperature,
* and geometry scaling are not characterized by the available measurements.
*
* Geometry: TOX=15nm from the PDK PDF; TSI=95nm and TBOX=400nm from
* AIST-SOI-CMOS.xs. XJ=50nm is an independent simulation placeholder, not a
* measured value; XJ <= TSI. SOIMOD=0 selects the partially-depleted mode.
* VTH0 comes from the p.7 table. U0/K1/RDSW are effective fits to p.10-11.
* NFACTOR follows the graph-derived subthreshold slope (not the p.7 table).
* K2=0 is explicit because no body-bias curves constrain the second-order term.
.model nch NMOS (
+ LEVEL=58 SOIMOD=0 MOBMOD=1 CAPMOD=2 TNOM=25
+ TOX=15e-9 TOXM=15e-9 TSI=95e-9 TBOX=400e-9 XJ=50e-9
+ VTH0=0.25 U0=670 NFACTOR=1.38 K1=0.125 K2=0 RDSW=0
+ )
.model pch PMOS (
+ LEVEL=58 SOIMOD=0 MOBMOD=1 CAPMOD=2 TNOM=25
+ TOX=15e-9 TOXM=15e-9 TSI=95e-9 TBOX=400e-9 XJ=50e-9
+ VTH0=-0.71 U0=268.75 NFACTOR=1.67 K1=0.14375 K2=0 RDSW=46250
+ )

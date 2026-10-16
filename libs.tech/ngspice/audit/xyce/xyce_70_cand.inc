* Xyce port of the frozen/candidate ICPS2023_5 BSIMSOI cards.
* LEVEL 70 substituted; all other parameters copied from the
* ngspice LEVEL=58 card.  Xyce startup logs are checked for
* unrecognised parameters before any result is used.
* This is NOT a model-equivalent copy of the ngspice card:
* BSIMSOI 4.6.1 differs from 4.4 by construction.
.model nch NMOS (
+ LEVEL=70 SOIMOD=0 MOBMOD=1 CAPMOD=2 TNOM=25 TOX=15e-9 TOXM=15e-9 TSI=95e-9 TBOX=400e-9 XJ=50e-9 VTH0=0.25 U0=670 NFACTOR=1.38 K1=0.125 K2=0 RDSW=0 CGSO=5e-9 CGDO=5e-9
+ )
.model pch PMOS (
+ LEVEL=70 SOIMOD=0 MOBMOD=1 CAPMOD=2 TNOM=25 TOX=15e-9 TOXM=15e-9 TSI=95e-9 TBOX=400e-9 XJ=50e-9 VTH0=-0.71 U0=268.75 NFACTOR=1.67 K1=0.14375 K2=0 RDSW=46250 CGSO=6.8e-9 CGDO=6.8e-9
+ )

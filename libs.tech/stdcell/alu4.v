// Four-operation unsigned 4-bit ALU for ICPS2023_5 native 1M cells.
// S1S0=00: AND; 01: OR; 10: A+B+CIN; 11: A-B-CIN (CIN is borrow-in).
// COUT is zero for logic operations, carry-out for addition, and no-borrow
// for subtraction. The subtraction carry chain uses A + ~B + ~CIN.
module ALU4 (
    input A0, input A1, input A2, input A3,
    input B0, input B1, input B2, input B3,
    input S0, input S1, input CIN,
    output Y0, output Y1, output Y2, output Y3,
    output COUT,
    inout VDD, inout VSS
);
    wire B0X = B0 ^ S0;
    wire B1X = B1 ^ S0;
    wire B2X = B2 ^ S0;
    wire B3X = B3 ^ S0;
    wire P0 = A0 ^ B0X;
    wire P1 = A1 ^ B1X;
    wire P2 = A2 ^ B2X;
    wire P3 = A3 ^ B3X;
    wire C0 = CIN ^ S0;
    wire C1 = (A0 & B0X) | (P0 & C0);
    wire C2 = (A1 & B1X) | (P1 & C1);
    wire C3 = (A2 & B2X) | (P2 & C2);
    wire C4 = (A3 & B3X) | (P3 & C3);
    wire SUM0 = P0 ^ C0;
    wire SUM1 = P1 ^ C1;
    wire SUM2 = P2 ^ C2;
    wire SUM3 = P3 ^ C3;

    assign Y0 = S1 ? SUM0 : (S0 ? (A0 | B0) : (A0 & B0));
    assign Y1 = S1 ? SUM1 : (S0 ? (A1 | B1) : (A1 & B1));
    assign Y2 = S1 ? SUM2 : (S0 ? (A2 | B2) : (A2 & B2));
    assign Y3 = S1 ? SUM3 : (S0 ? (A3 | B3) : (A3 & B3));
    assign COUT = S1 & C4;
endmodule

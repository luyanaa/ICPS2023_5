module alu8(
  input A0, input A1, input A2, input A3, input A4, input A5, input A6, input A7,
  input B0, input B1, input B2, input B3, input B4, input B5, input B6, input B7,
  input S0, input S1, input CIN,
  output Y0, output Y1, output Y2, output Y3,
  output Y4, output Y5, output Y6, output Y7, output COUT
);
  wire [7:0] A = {A7, A6, A5, A4, A3, A2, A1, A0};
  wire [7:0] B = {B7, B6, B5, B4, B3, B2, B1, B0};
  wire [8:0] arithmetic = {1'b0, A} + {1'b0, (B ^ {8{S0}})} + {8'b0, (CIN ^ S0)};
  wire [7:0] logic_result = S0 ? (A | B) : (A & B);
  wire [7:0] result = S1 ? arithmetic[7:0] : logic_result;
  assign Y0 = result[0];
  assign Y1 = result[1];
  assign Y2 = result[2];
  assign Y3 = result[3];
  assign Y4 = result[4];
  assign Y5 = result[5];
  assign Y6 = result[6];
  assign Y7 = result[7];
  assign COUT = S1 ? arithmetic[8] : 1'b0;
endmodule

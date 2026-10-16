module mux4_decoder(
  input D0, input D1, input D2, input D3,
  input S0, input S1,
  output Y, output SEL0, output SEL1, output SEL2, output SEL3
);
  assign SEL0 = ~S1 & ~S0;
  assign SEL1 = ~S1 & S0;
  assign SEL2 = S1 & ~S0;
  assign SEL3 = S1 & S0;
  assign Y = (D0 & SEL0) | (D1 & SEL1) | (D2 & SEL2) | (D3 & SEL3);
endmodule

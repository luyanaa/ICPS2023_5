module shift_register4(
  input CLK, input DIN,
  output reg Q0, output reg Q1, output reg Q2, output reg Q3
);
  always @(posedge CLK) begin
    Q0 <= DIN;
    Q1 <= Q0;
    Q2 <= Q1;
    Q3 <= Q2;
  end
endmodule

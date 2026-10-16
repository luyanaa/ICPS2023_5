module counter(
  input CLK, input RESET,
  output reg Q0, output reg Q1, output reg Q2, output reg Q3
);
  always @(posedge CLK) begin
    Q0 <= ~RESET & ~Q0;
    Q1 <= ~RESET & (Q1 ^ Q0);
    Q2 <= ~RESET & (Q2 ^ (Q1 & Q0));
    Q3 <= ~RESET & (Q3 ^ (Q2 & Q1 & Q0));
  end
endmodule

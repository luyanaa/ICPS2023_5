module inv_nand2_aoi21(
  input A, input B, input C,
  output INV_Y, output NAND_Y, output AOI21_Y
);
  assign INV_Y = ~A;
  assign NAND_Y = ~(A & B);
  assign AOI21_Y = ~((A & B) | C);
endmodule

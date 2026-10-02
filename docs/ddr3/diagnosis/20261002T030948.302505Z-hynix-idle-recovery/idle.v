module ddr_idle(output ddr_reset_n, output ddr_cke, output ddr_cs,
                output ddr_ck, output ddr_odt);
assign ddr_reset_n = 1'b0;
assign ddr_cke = 1'b0;
assign ddr_cs = 1'b1;
assign ddr_ck = 1'b0;
assign ddr_odt = 1'b0;
endmodule

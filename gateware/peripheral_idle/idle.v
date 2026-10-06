// No clocks or state machines: hold external interfaces in safe idle states.
module peripheral_idle(output ddr_reset_n, ddr_cke, ddr_cs, ddr_ck, ddr_odt,
                       output sd_cs, sd_clk, sd_mosi, phy_reset_n,
                       output eth_txen, output [1:0] eth_txd, output eth_mdc,
                       output uart_tx);
assign ddr_reset_n = 1'b0;
assign ddr_cke = 1'b0;
assign ddr_cs = 1'b1;
assign ddr_ck = 1'b0;
assign ddr_odt = 1'b0;
assign sd_cs = 1'b1;
assign sd_clk = 1'b0;
assign sd_mosi = 1'b1;
assign phy_reset_n = 1'b0;
assign eth_txen = 1'b0;
assign eth_txd = 2'b00;
assign eth_mdc = 1'b0;
assign uart_tx = 1'b1;
endmodule

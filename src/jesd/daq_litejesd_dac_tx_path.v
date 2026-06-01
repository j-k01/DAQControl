`timescale 1ns/1ps

module daq_litejesd_dac_tx_path #(
    parameter [15:0] DEFAULT_STEP = 16'd256
) (
    input  wire          jesd_clk,
    input  wire          jesd_rst,

    input  wire          phy_tx_clk,
    input  wire [7:0]    phy_tx_rst,

    input  wire          enable,
    input  wire          stpl_enable,
    input  wire          sysref,
    input  wire          sync_n,

    input  wire [2:0]    active_converter,
    input  wire [15:0]   triangle_step,

    output wire          litejesd_ready,
    output wire [31:0]   status,
    output wire [31:0]   triangle_word,

    output wire [255:0]  gth_txdata,
    output wire [31:0]   gth_txcharisk
);

    wire [15:0] step = (triangle_step == 16'd0) ? DEFAULT_STEP : triangle_step;

    reg  [15:0] triangle_sample = 16'd0;
    reg         triangle_up = 1'b1;
    reg  [31:0] triangle_word_r = 32'd0;

    function [16:0] advance_triangle;
        input [15:0] sample;
        input        up;
        input [15:0] inc;
        begin
            if (up) begin
                if (sample >= (16'hffff - inc)) begin
                    advance_triangle = {1'b0, 16'hffff};
                end else begin
                    advance_triangle = {1'b1, sample + inc};
                end
            end else begin
                if (sample <= inc) begin
                    advance_triangle = {1'b1, 16'd0};
                end else begin
                    advance_triangle = {1'b0, sample - inc};
                end
            end
        end
    endfunction

    wire [16:0] triangle_next0 = advance_triangle(triangle_sample, triangle_up, step);
    wire [16:0] triangle_next1 = advance_triangle(triangle_next0[15:0], triangle_next0[16], step);
    wire [16:0] triangle_next2 = advance_triangle(triangle_next1[15:0], triangle_next1[16], step);
    wire [16:0] triangle_next3 = advance_triangle(triangle_next2[15:0], triangle_next2[16], step);

    always @(posedge jesd_clk) begin
        if (jesd_rst || !enable) begin
            triangle_sample <= 16'd0;
            triangle_up     <= 1'b1;
            triangle_word_r <= 32'd0;
        end else begin
            triangle_word_r <= {triangle_next0[15:0], triangle_sample};
            triangle_sample <= triangle_next3[15:0];
            triangle_up     <= triangle_next3[16];
        end
    end

    assign triangle_word = triangle_word_r;

    wire [63:0] triangle_quad_word = {
        triangle_next2[15:0],
        triangle_next1[15:0],
        triangle_next0[15:0],
        triangle_sample
    };
    wire [63:0] midscale_quad_word = {4{16'h8000}};

    wire [63:0] converter0 = (active_converter == 3'd0) ? triangle_quad_word : midscale_quad_word;
    wire [63:0] converter1 = (active_converter == 3'd1) ? triangle_quad_word : midscale_quad_word;
    wire [63:0] converter2 = (active_converter == 3'd2) ? triangle_quad_word : midscale_quad_word;
    wire [63:0] converter3 = (active_converter == 3'd3) ? triangle_quad_word : midscale_quad_word;

    wire [31:0] tx_data0;
    wire [31:0] tx_data1;
    wire [31:0] tx_data2;
    wire [31:0] tx_data3;
    wire [31:0] tx_data4;
    wire [31:0] tx_data5;
    wire [31:0] tx_data6;
    wire [31:0] tx_data7;

    wire [3:0] tx_ctrl0;
    wire [3:0] tx_ctrl1;
    wire [3:0] tx_ctrl2;
    wire [3:0] tx_ctrl3;
    wire [3:0] tx_ctrl4;
    wire [3:0] tx_ctrl5;
    wire [3:0] tx_ctrl6;
    wire [3:0] tx_ctrl7;

    litejesd_dac_tx u_litejesd_dac_tx (
        .converter0       (converter0),
        .converter1       (converter1),
        .converter2       (converter2),
        .converter3       (converter3),
        .enable           (enable),
        .jesd_clk         (jesd_clk),
        .jesd_rst         (jesd_rst),
        .jesd_phy0_tx_clk (phy_tx_clk),
        .jesd_phy0_tx_rst (phy_tx_rst[0]),
        .jesd_phy1_tx_clk (phy_tx_clk),
        .jesd_phy1_tx_rst (phy_tx_rst[1]),
        .jesd_phy2_tx_clk (phy_tx_clk),
        .jesd_phy2_tx_rst (phy_tx_rst[2]),
        .jesd_phy3_tx_clk (phy_tx_clk),
        .jesd_phy3_tx_rst (phy_tx_rst[3]),
        .jesd_phy4_tx_clk (phy_tx_clk),
        .jesd_phy4_tx_rst (phy_tx_rst[4]),
        .jesd_phy5_tx_clk (phy_tx_clk),
        .jesd_phy5_tx_rst (phy_tx_rst[5]),
        .jesd_phy6_tx_clk (phy_tx_clk),
        .jesd_phy6_tx_rst (phy_tx_rst[6]),
        .jesd_phy7_tx_clk (phy_tx_clk),
        .jesd_phy7_tx_rst (phy_tx_rst[7]),
        .ready            (litejesd_ready),
        .stpl_enable      (stpl_enable),
        .sync_n           (sync_n),
        .sysref           (sysref),
        .tx_ctrl0         (tx_ctrl0),
        .tx_ctrl1         (tx_ctrl1),
        .tx_ctrl2         (tx_ctrl2),
        .tx_ctrl3         (tx_ctrl3),
        .tx_ctrl4         (tx_ctrl4),
        .tx_ctrl5         (tx_ctrl5),
        .tx_ctrl6         (tx_ctrl6),
        .tx_ctrl7         (tx_ctrl7),
        .tx_data0         (tx_data0),
        .tx_data1         (tx_data1),
        .tx_data2         (tx_data2),
        .tx_data3         (tx_data3),
        .tx_data4         (tx_data4),
        .tx_data5         (tx_data5),
        .tx_data6         (tx_data6),
        .tx_data7         (tx_data7)
    );

    assign gth_txdata = {
        tx_data7,
        tx_data6,
        tx_data5,
        tx_data4,
        tx_data3,
        tx_data2,
        tx_data1,
        tx_data0
    };

    assign gth_txcharisk = {
        tx_ctrl7,
        tx_ctrl6,
        tx_ctrl5,
        tx_ctrl4,
        tx_ctrl3,
        tx_ctrl2,
        tx_ctrl1,
        tx_ctrl0
    };

    assign status = {
        8'd0,
        active_converter,
        stpl_enable,
        sync_n,
        sysref,
        litejesd_ready,
        enable,
        phy_tx_rst,
        triangle_up,
        jesd_rst,
        6'd0
    };

endmodule

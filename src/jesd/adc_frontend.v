`timescale 1ns/1ps

module adc_frontend (
    input  wire        jesd_clk,
    input  wire        jesd_rst,
    input  wire        enable,
    input  wire        sysref,
    input  wire        ilas_check_enable,
    input  wire        stpl_enable,
    input  wire [1:0]  raw_lane_select,
    input  wire [1:0]  capture_format,
    input  wire        adc0_use_physical_dp_order,
    input  wire        adc1_use_physical_dp_order,

    input  wire [255:0] gth_userdata_rx,
    input  wire [127:0] gth_rxctrl0,
    input  wire [127:0] gth_rxctrl1,
    input  wire [63:0]  gth_rxctrl3,
    input  wire [7:0]   gth_rxbyteisaligned,
    input  wire [7:0]   gth_rxcdrlock,
    input  wire [7:0]   gth_rxpmaresetdone,

    output wire        adc0_sync_n,
    output wire        adc1_sync_n,
    output wire        adc0_ready,
    output wire        adc1_ready,

    output wire [31:0] adc0_status,
    output wire [31:0] adc0_lane_status,
    output wire [31:0] adc0_event_counts,
    output wire [31:0] adc0_ch_a_low,
    output wire [31:0] adc0_ch_a_high,
    output wire [31:0] adc0_ch_b_low,
    output wire [31:0] adc0_ch_b_high,
    output wire [31:0] adc0_raw_lane,

    output wire [31:0] adc1_status,
    output wire [31:0] adc1_lane_status,
    output wire [31:0] adc1_event_counts,
    output wire [31:0] adc1_ch_a_low,
    output wire [31:0] adc1_ch_a_high,
    output wire [31:0] adc1_ch_b_low,
    output wire [31:0] adc1_ch_b_high,
    output wire [31:0] adc1_raw_lane,

    output wire [63:0] adc_ch0,
    output wire [63:0] adc_ch1,
    output wire [63:0] adc_ch2,
    output wire [63:0] adc_ch3
);

    function [31:0] lane_data;
        input [2:0] lane;
        begin
            lane_data = gth_userdata_rx[(32*lane) +: 32];
        end
    endfunction

    function [3:0] lane_charisk;
        input [2:0] lane;
        begin
            lane_charisk = gth_rxctrl0[(16*lane) +: 4];
        end
    endfunction

    function [3:0] lane_disperr;
        input [2:0] lane;
        begin
            lane_disperr = gth_rxctrl1[(16*lane) +: 4];
        end
    endfunction

    function [3:0] lane_notintable;
        input [2:0] lane;
        begin
            lane_notintable = gth_rxctrl3[(8*lane) +: 4];
        end
    endfunction

    wire [31:0] dp0_data = lane_data(3'd4);
    wire [31:0] dp1_data = lane_data(3'd5);
    wire [31:0] dp2_data = lane_data(3'd6);
    wire [31:0] dp3_data = lane_data(3'd7);
    wire [31:0] dp4_data = lane_data(3'd0);
    wire [31:0] dp5_data = lane_data(3'd1);
    wire [31:0] dp6_data = lane_data(3'd2);
    wire [31:0] dp7_data = lane_data(3'd3);

    // ADC chip0 is on DP0-DP3.  Default order follows the Sundance signal
    // names: A_OUT1/DP0, A_OUT2/DP3, B_OUT1/DP2, B_OUT2/DP1.
    wire [31:0] adc0_rx_data0 = dp0_data;
    wire [31:0] adc0_rx_data1 = adc0_use_physical_dp_order ? dp1_data : dp3_data;
    wire [31:0] adc0_rx_data2 = dp2_data;
    wire [31:0] adc0_rx_data3 = adc0_use_physical_dp_order ? dp3_data : dp1_data;

    wire [3:0] adc0_rx_charisk0 = lane_charisk(3'd4);
    wire [3:0] adc0_rx_charisk1 = adc0_use_physical_dp_order ? lane_charisk(3'd5) : lane_charisk(3'd7);
    wire [3:0] adc0_rx_charisk2 = lane_charisk(3'd6);
    wire [3:0] adc0_rx_charisk3 = adc0_use_physical_dp_order ? lane_charisk(3'd7) : lane_charisk(3'd5);
    wire [3:0] adc0_rx_disperr0 = lane_disperr(3'd4);
    wire [3:0] adc0_rx_disperr1 = adc0_use_physical_dp_order ? lane_disperr(3'd5) : lane_disperr(3'd7);
    wire [3:0] adc0_rx_disperr2 = lane_disperr(3'd6);
    wire [3:0] adc0_rx_disperr3 = adc0_use_physical_dp_order ? lane_disperr(3'd7) : lane_disperr(3'd5);
    wire [3:0] adc0_rx_notintable0 = lane_notintable(3'd4);
    wire [3:0] adc0_rx_notintable1 = adc0_use_physical_dp_order ? lane_notintable(3'd5) : lane_notintable(3'd7);
    wire [3:0] adc0_rx_notintable2 = lane_notintable(3'd6);
    wire [3:0] adc0_rx_notintable3 = adc0_use_physical_dp_order ? lane_notintable(3'd7) : lane_notintable(3'd5);
    wire [3:0] adc0_rx_byteisaligned = adc0_use_physical_dp_order ?
        gth_rxbyteisaligned[7:4] :
        {gth_rxbyteisaligned[5], gth_rxbyteisaligned[6], gth_rxbyteisaligned[7], gth_rxbyteisaligned[4]};
    wire [3:0] adc0_rx_cdrlock = adc0_use_physical_dp_order ?
        gth_rxcdrlock[7:4] :
        {gth_rxcdrlock[5], gth_rxcdrlock[6], gth_rxcdrlock[7], gth_rxcdrlock[4]};
    wire [3:0] adc0_rx_pmaresetdone = adc0_use_physical_dp_order ?
        gth_rxpmaresetdone[7:4] :
        {gth_rxpmaresetdone[5], gth_rxpmaresetdone[6], gth_rxpmaresetdone[7], gth_rxpmaresetdone[4]};

    // ADC chip1 is on DP4-DP7.  Default order follows the FMC-ADC500-CD
    // signal names: A_OUT1/DP6, A_OUT2/DP5, B_OUT1/DP4, B_OUT2/DP7.
    wire [31:0] adc1_rx_data0 = adc1_use_physical_dp_order ? dp4_data : dp6_data;
    wire [31:0] adc1_rx_data1 = adc1_use_physical_dp_order ? dp5_data : dp5_data;
    wire [31:0] adc1_rx_data2 = adc1_use_physical_dp_order ? dp6_data : dp4_data;
    wire [31:0] adc1_rx_data3 = adc1_use_physical_dp_order ? dp7_data : dp7_data;

    wire [3:0] adc1_rx_charisk0 = adc1_use_physical_dp_order ? lane_charisk(3'd0) : lane_charisk(3'd2);
    wire [3:0] adc1_rx_charisk1 = lane_charisk(3'd1);
    wire [3:0] adc1_rx_charisk2 = adc1_use_physical_dp_order ? lane_charisk(3'd2) : lane_charisk(3'd0);
    wire [3:0] adc1_rx_charisk3 = lane_charisk(3'd3);
    wire [3:0] adc1_rx_disperr0 = adc1_use_physical_dp_order ? lane_disperr(3'd0) : lane_disperr(3'd2);
    wire [3:0] adc1_rx_disperr1 = lane_disperr(3'd1);
    wire [3:0] adc1_rx_disperr2 = adc1_use_physical_dp_order ? lane_disperr(3'd2) : lane_disperr(3'd0);
    wire [3:0] adc1_rx_disperr3 = lane_disperr(3'd3);
    wire [3:0] adc1_rx_notintable0 = adc1_use_physical_dp_order ? lane_notintable(3'd0) : lane_notintable(3'd2);
    wire [3:0] adc1_rx_notintable1 = lane_notintable(3'd1);
    wire [3:0] adc1_rx_notintable2 = adc1_use_physical_dp_order ? lane_notintable(3'd2) : lane_notintable(3'd0);
    wire [3:0] adc1_rx_notintable3 = lane_notintable(3'd3);
    wire [3:0] adc1_rx_byteisaligned = adc1_use_physical_dp_order ?
        gth_rxbyteisaligned[3:0] :
        {gth_rxbyteisaligned[3], gth_rxbyteisaligned[0], gth_rxbyteisaligned[1], gth_rxbyteisaligned[2]};
    wire [3:0] adc1_rx_cdrlock = adc1_use_physical_dp_order ?
        gth_rxcdrlock[3:0] :
        {gth_rxcdrlock[3], gth_rxcdrlock[0], gth_rxcdrlock[1], gth_rxcdrlock[2]};
    wire [3:0] adc1_rx_pmaresetdone = adc1_use_physical_dp_order ?
        gth_rxpmaresetdone[3:0] :
        {gth_rxpmaresetdone[3], gth_rxpmaresetdone[0], gth_rxpmaresetdone[1], gth_rxpmaresetdone[2]};

    daq_litejesd_adc1_rx_path #(
        .STATUS_TAG (8'hA0),
        .SWAP_CHANNEL_B_BYTES (1'b1)
    ) u_adc0_rx_path (
        .jesd_clk           (jesd_clk),
        .jesd_rst           (jesd_rst),
        .enable             (enable),
        .sysref             (sysref),
        .ilas_check_enable  (ilas_check_enable),
        .stpl_enable        (stpl_enable),
        .raw_lane_select    (raw_lane_select),
        .capture_format     (capture_format),
        .rx_data0           (adc0_rx_data0),
        .rx_data1           (adc0_rx_data1),
        .rx_data2           (adc0_rx_data2),
        .rx_data3           (adc0_rx_data3),
        .rx_charisk0        (adc0_rx_charisk0),
        .rx_charisk1        (adc0_rx_charisk1),
        .rx_charisk2        (adc0_rx_charisk2),
        .rx_charisk3        (adc0_rx_charisk3),
        .rx_disperr0        (adc0_rx_disperr0),
        .rx_disperr1        (adc0_rx_disperr1),
        .rx_disperr2        (adc0_rx_disperr2),
        .rx_disperr3        (adc0_rx_disperr3),
        .rx_notintable0     (adc0_rx_notintable0),
        .rx_notintable1     (adc0_rx_notintable1),
        .rx_notintable2     (adc0_rx_notintable2),
        .rx_notintable3     (adc0_rx_notintable3),
        .rx_byteisaligned   (adc0_rx_byteisaligned),
        .rx_cdrlock         (adc0_rx_cdrlock),
        .rx_pmaresetdone    (adc0_rx_pmaresetdone),
        .adc_sync_n         (adc0_sync_n),
        .litejesd_ready     (adc0_ready),
        .status             (adc0_status),
        .lane_status        (adc0_lane_status),
        .event_counts       (adc0_event_counts),
        .sample_a_low       (adc0_ch_a_low),
        .sample_a_high      (adc0_ch_a_high),
        .sample_b_low       (adc0_ch_b_low),
        .sample_b_high      (adc0_ch_b_high),
        .raw_lane_data      (adc0_raw_lane)
    );

    daq_litejesd_adc1_rx_path #(
        .STATUS_TAG (8'hA1),
        .SWAP_CHANNEL_B_BYTES (1'b1)
    ) u_adc1_rx_path (
        .jesd_clk           (jesd_clk),
        .jesd_rst           (jesd_rst),
        .enable             (enable),
        .sysref             (sysref),
        .ilas_check_enable  (ilas_check_enable),
        .stpl_enable        (stpl_enable),
        .raw_lane_select    (raw_lane_select),
        .capture_format     (capture_format),
        .rx_data0           (adc1_rx_data0),
        .rx_data1           (adc1_rx_data1),
        .rx_data2           (adc1_rx_data2),
        .rx_data3           (adc1_rx_data3),
        .rx_charisk0        (adc1_rx_charisk0),
        .rx_charisk1        (adc1_rx_charisk1),
        .rx_charisk2        (adc1_rx_charisk2),
        .rx_charisk3        (adc1_rx_charisk3),
        .rx_disperr0        (adc1_rx_disperr0),
        .rx_disperr1        (adc1_rx_disperr1),
        .rx_disperr2        (adc1_rx_disperr2),
        .rx_disperr3        (adc1_rx_disperr3),
        .rx_notintable0     (adc1_rx_notintable0),
        .rx_notintable1     (adc1_rx_notintable1),
        .rx_notintable2     (adc1_rx_notintable2),
        .rx_notintable3     (adc1_rx_notintable3),
        .rx_byteisaligned   (adc1_rx_byteisaligned),
        .rx_cdrlock         (adc1_rx_cdrlock),
        .rx_pmaresetdone    (adc1_rx_pmaresetdone),
        .adc_sync_n         (adc1_sync_n),
        .litejesd_ready     (adc1_ready),
        .status             (adc1_status),
        .lane_status        (adc1_lane_status),
        .event_counts       (adc1_event_counts),
        .sample_a_low       (adc1_ch_a_low),
        .sample_a_high      (adc1_ch_a_high),
        .sample_b_low       (adc1_ch_b_low),
        .sample_b_high      (adc1_ch_b_high),
        .raw_lane_data      (adc1_raw_lane)
    );

    assign adc_ch0 = {adc0_ch_a_high, adc0_ch_a_low};
    assign adc_ch1 = {adc0_ch_b_high, adc0_ch_b_low};
    assign adc_ch2 = {adc1_ch_a_high, adc1_ch_a_low};
    assign adc_ch3 = {adc1_ch_b_high, adc1_ch_b_low};

endmodule

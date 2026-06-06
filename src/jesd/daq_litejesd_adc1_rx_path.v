`timescale 1ns/1ps

module daq_litejesd_adc1_rx_path (
    input  wire        jesd_clk,
    input  wire        jesd_rst,
    input  wire        enable,
    input  wire        sysref,
    input  wire        ilas_check_enable,
    input  wire        stpl_enable,
    input  wire [1:0]  raw_lane_select,
    input  wire [1:0]  capture_format,

    input  wire [31:0] rx_data0,
    input  wire [31:0] rx_data1,
    input  wire [31:0] rx_data2,
    input  wire [31:0] rx_data3,
    input  wire [3:0]  rx_charisk0,
    input  wire [3:0]  rx_charisk1,
    input  wire [3:0]  rx_charisk2,
    input  wire [3:0]  rx_charisk3,
    input  wire [3:0]  rx_disperr0,
    input  wire [3:0]  rx_disperr1,
    input  wire [3:0]  rx_disperr2,
    input  wire [3:0]  rx_disperr3,
    input  wire [3:0]  rx_notintable0,
    input  wire [3:0]  rx_notintable1,
    input  wire [3:0]  rx_notintable2,
    input  wire [3:0]  rx_notintable3,
    input  wire [3:0]  rx_byteisaligned,
    input  wire [3:0]  rx_cdrlock,
    input  wire [3:0]  rx_pmaresetdone,

    output wire        adc_sync_n,
    output wire        litejesd_ready,
    output wire [31:0] status,
    output wire [31:0] lane_status,
    output wire [31:0] event_counts,
    output wire [31:0] sample_a_low,
    output wire [31:0] sample_a_high,
    output wire [31:0] sample_b_low,
    output wire [31:0] sample_b_high,
    output wire [31:0] raw_lane_data
);

    wire        core_ready;
    wire [3:0] link_ready;
    wire [3:0] link_sync;
    wire [3:0] rx_align;
    wire [63:0] converter0;
    wire [63:0] converter1;
    wire [31:0] debug_transport_lane0;
    wire [31:0] debug_transport_lane1;
    wire [31:0] debug_transport_lane2;
    wire [31:0] debug_transport_lane3;

    litejesd_adc1_rx u_litejesd_adc1_rx (
        .jesd_clk            (jesd_clk),
        .jesd_rst            (jesd_rst),
        .jesd_phy0_rx_clk    (jesd_clk),
        .jesd_phy0_rx_rst    (jesd_rst),
        .jesd_phy1_rx_clk    (jesd_clk),
        .jesd_phy1_rx_rst    (jesd_rst),
        .jesd_phy2_rx_clk    (jesd_clk),
        .jesd_phy2_rx_rst    (jesd_rst),
        .jesd_phy3_rx_clk    (jesd_clk),
        .jesd_phy3_rx_rst    (jesd_rst),
        .enable              (enable),
        .ilas_check_enable   (ilas_check_enable),
        .stpl_enable         (stpl_enable),
        .sysref              (sysref),
        .sync_n              (adc_sync_n),
        .ready               (core_ready),
        .link_ready          (link_ready),
        .link_sync           (link_sync),
        .rx_align            (rx_align),
        .debug_transport_lane0 (debug_transport_lane0),
        .debug_transport_lane1 (debug_transport_lane1),
        .debug_transport_lane2 (debug_transport_lane2),
        .debug_transport_lane3 (debug_transport_lane3),
        .rx_data0            (rx_data0),
        .rx_data1            (rx_data1),
        .rx_data2            (rx_data2),
        .rx_data3            (rx_data3),
        .rx_ctrl0            (rx_charisk0),
        .rx_ctrl1            (rx_charisk1),
        .rx_ctrl2            (rx_charisk2),
        .rx_ctrl3            (rx_charisk3),
        .converter0          (converter0),
        .converter1          (converter1)
    );

    assign litejesd_ready = core_ready;

    function has_k28_5;
        input [31:0] data;
        input [3:0]  ctrl;
        begin
            has_k28_5 = (ctrl[0] && data[7:0]   == 8'hbc) ||
                        (ctrl[1] && data[15:8]  == 8'hbc) ||
                        (ctrl[2] && data[23:16] == 8'hbc) ||
                        (ctrl[3] && data[31:24] == 8'hbc);
        end
    endfunction

    function [7:0] sat_inc8;
        input [7:0] value;
        begin
            sat_inc8 = (value == 8'hff) ? 8'hff : value + 1'b1;
        end
    endfunction

    reg [3:0] k_seen = 4'd0;
    reg [3:0] data_seen = 4'd0;
    reg [3:0] err_seen = 4'd0;
    reg [7:0] ready_count = 8'd0;
    reg [7:0] sync_high_count = 8'd0;
    reg [7:0] sysref_edge_count = 8'd0;
    reg [7:0] error_event_count = 8'd0;
    reg       sysref_d = 1'b0;
    reg [63:0] sample_a = 64'd0;
    reg [63:0] sample_b = 64'd0;
    reg [31:0] transport_lane0 = 32'd0;
    reg [31:0] transport_lane1 = 32'd0;
    reg [31:0] transport_lane2 = 32'd0;
    reg [31:0] transport_lane3 = 32'd0;
    reg [31:0] raw0 = 32'd0;
    reg [31:0] raw1 = 32'd0;
    reg [31:0] raw2 = 32'd0;
    reg [31:0] raw3 = 32'd0;

    wire lane0_k = has_k28_5(rx_data0, rx_charisk0);
    wire lane1_k = has_k28_5(rx_data1, rx_charisk1);
    wire lane2_k = has_k28_5(rx_data2, rx_charisk2);
    wire lane3_k = has_k28_5(rx_data3, rx_charisk3);
    wire [3:0] lane_has_k = {lane3_k, lane2_k, lane1_k, lane0_k};
    wire [3:0] lane_has_data = {
        rx_charisk3 == 4'd0,
        rx_charisk2 == 4'd0,
        rx_charisk1 == 4'd0,
        rx_charisk0 == 4'd0
    };
    wire [3:0] lane_has_err = {
        |rx_disperr3 | |rx_notintable3,
        |rx_disperr2 | |rx_notintable2,
        |rx_disperr1 | |rx_notintable1,
        |rx_disperr0 | |rx_notintable0
    };
    wire [3:0] lane_notintable = {
        |rx_notintable3,
        |rx_notintable2,
        |rx_notintable1,
        |rx_notintable0
    };
    wire [3:0] lane_disperr = {
        |rx_disperr3,
        |rx_disperr2,
        |rx_disperr1,
        |rx_disperr0
    };

    always @(posedge jesd_clk) begin
        if (jesd_rst) begin
            k_seen <= 4'd0;
            data_seen <= 4'd0;
            err_seen <= 4'd0;
            ready_count <= 8'd0;
            sync_high_count <= 8'd0;
            sysref_edge_count <= 8'd0;
            error_event_count <= 8'd0;
            sysref_d <= 1'b0;
            sample_a <= 64'd0;
            sample_b <= 64'd0;
            transport_lane0 <= 32'd0;
            transport_lane1 <= 32'd0;
            transport_lane2 <= 32'd0;
            transport_lane3 <= 32'd0;
            raw0 <= 32'd0;
            raw1 <= 32'd0;
            raw2 <= 32'd0;
            raw3 <= 32'd0;
        end else begin
            sysref_d <= sysref;
            raw0 <= rx_data0;
            raw1 <= rx_data1;
            raw2 <= rx_data2;
            raw3 <= rx_data3;
            k_seen <= k_seen | lane_has_k;
            data_seen <= data_seen | lane_has_data;
            err_seen <= err_seen | lane_has_err;

            if (core_ready) begin
                ready_count <= sat_inc8(ready_count);
                sample_a <= converter0;
                sample_b <= converter1;
                transport_lane0 <= debug_transport_lane0;
                transport_lane1 <= debug_transport_lane1;
                transport_lane2 <= debug_transport_lane2;
                transport_lane3 <= debug_transport_lane3;
            end
            if (adc_sync_n) begin
                sync_high_count <= sat_inc8(sync_high_count);
            end
            if (sysref && !sysref_d) begin
                sysref_edge_count <= sat_inc8(sysref_edge_count);
            end
            if (|lane_has_err) begin
                error_event_count <= sat_inc8(error_event_count);
            end
        end
    end

    assign status = {
        8'hA1,
        core_ready,
        adc_sync_n,
        jesd_rst,
        ilas_check_enable,
        stpl_enable,
        &rx_byteisaligned,
        &rx_cdrlock,
        &rx_pmaresetdone,
        link_ready,
        link_sync,
        k_seen,
        data_seen
    };

    assign lane_status = {
        4'h1,
        rx_align,
        err_seen,
        lane_notintable,
        lane_disperr,
        rx_byteisaligned,
        rx_cdrlock,
        rx_pmaresetdone
    };

    assign event_counts = {
        ready_count,
        sync_high_count,
        sysref_edge_count,
        error_event_count
    };

    wire [63:0] sun_ch1_normal;
    wire [63:0] sun_ch2_normal;
    wire [63:0] sun_ch1_revbyte;
    wire [63:0] sun_ch2_revbyte;

    function [15:0] swap_sample_bytes16;
        input [15:0] value;
        begin
            swap_sample_bytes16 = {value[7:0], value[15:8]};
        end
    endfunction

    wire [15:0] sample_b0 = sample_b[15:0];
    wire [15:0] sample_b1 = sample_b[31:16];
    wire [15:0] sample_b2 = sample_b[47:32];
    wire [15:0] sample_b3 = sample_b[63:48];

    // ADS54J60 LMFS=4211 on the FMC-ADC500-CD needs one last publication
    // step after LiteJESD link/transport decoding.  Sundance's reference
    // design rebuilds ADC samples from transport lane-byte regions; board
    // tests with distinct DAC0/DAC1 tones show the generated LiteJESD sample
    // order is already chronological for channel A, while channel B's 16-bit
    // samples need byte swapping.
    //
    // Published order, four chronological samples per jesd_clk:
    //   CH A: generated slots [0,1,2,3]
    //   CH B: generated slots [0,1,2,3], with sample bytes swapped
    wire [63:0] sample_a_lmfs4211 = sample_a;
    wire [63:0] sample_b_lmfs4211 = {
        swap_sample_bytes16(sample_b3),
        swap_sample_bytes16(sample_b2),
        swap_sample_bytes16(sample_b1),
        swap_sample_bytes16(sample_b0)
    };

    adc1_sundance_halfbeat #(
        .REVERSE_BYTES (0),
        .SWAP_SAMPLE_BYTES (0)
    ) u_adc1_sundance_normal (
        .lane0    (transport_lane0),
        .lane1    (transport_lane1),
        .lane2    (transport_lane2),
        .lane3    (transport_lane3),
        .adc1_ch1 (sun_ch1_normal),
        .adc1_ch2 (sun_ch2_normal)
    );

    adc1_sundance_halfbeat #(
        .REVERSE_BYTES (1),
        .SWAP_SAMPLE_BYTES (0)
    ) u_adc1_sundance_revbyte (
        .lane0    (transport_lane0),
        .lane1    (transport_lane1),
        .lane2    (transport_lane2),
        .lane3    (transport_lane3),
        .adc1_ch1 (sun_ch1_revbyte),
        .adc1_ch2 (sun_ch2_revbyte)
    );

    reg [31:0] cap_a_low;
    reg [31:0] cap_a_high;
    reg [31:0] cap_b_low;
    reg [31:0] cap_b_high;

    always @(*) begin
        case (capture_format)
        2'd1: begin
            cap_a_low = transport_lane0;
            cap_a_high = transport_lane1;
            cap_b_low = transport_lane2;
            cap_b_high = transport_lane3;
        end
        2'd2: begin
            // Legacy generated-LiteJESD converter order, kept for diagnostics.
            cap_a_low = sample_a[31:0];
            cap_a_high = sample_a[63:32];
            cap_b_low = sample_b[31:0];
            cap_b_high = sample_b[63:32];
        end
        2'd3: begin
            cap_a_low = sun_ch1_revbyte[31:0];
            cap_a_high = sun_ch1_revbyte[63:32];
            cap_b_low = sun_ch2_revbyte[31:0];
            cap_b_high = sun_ch2_revbyte[63:32];
        end
        default: begin
            cap_a_low = sample_a_lmfs4211[31:0];
            cap_a_high = sample_a_lmfs4211[63:32];
            cap_b_low = sample_b_lmfs4211[31:0];
            cap_b_high = sample_b_lmfs4211[63:32];
        end
        endcase
    end

    assign sample_a_low = cap_a_low;
    assign sample_a_high = cap_a_high;
    assign sample_b_low = cap_b_low;
    assign sample_b_high = cap_b_high;
    assign raw_lane_data = (raw_lane_select == 2'd0) ? raw0 :
                           (raw_lane_select == 2'd1) ? raw1 :
                           (raw_lane_select == 2'd2) ? raw2 : raw3;

endmodule

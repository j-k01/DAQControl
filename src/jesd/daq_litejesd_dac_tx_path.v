`timescale 1ns/1ps

module daq_litejesd_dac_tx_path #(
    parameter [15:0] DEFAULT_STEP = 16'd256,
    parameter [23:0] DEFAULT_SINE_PHASE_INC = 24'h19999A
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
    input  wire [1:0]    sample_map_mode,
    input  wire [15:0]   triangle_step,
    input  wire [23:0]   sine_phase_inc,
    input  wire [7:0]    source_modes,
    input  wire          program_enable,
    input  wire [63:0]   program_word0,
    input  wire [63:0]   program_word1,
    input  wire [63:0]   program_word2,
    input  wire [63:0]   program_word3,
    input  wire [63:0]   neuron_word0,
    input  wire [63:0]   neuron_word1,
    input  wire [63:0]   neuron_word2,
    input  wire [63:0]   neuron_word3,

    output wire          litejesd_ready,
    output wire [31:0]   status,
    output wire [31:0]   triangle_word,
    output wire [31:0]   sine_word,

    output wire [255:0]  gth_txdata,
    output wire [31:0]   gth_txcharisk
);

    wire [15:0] step = (triangle_step == 16'd0) ? DEFAULT_STEP : triangle_step;
    wire [23:0] sine_step = (sine_phase_inc == 24'd0) ?
        DEFAULT_SINE_PHASE_INC : sine_phase_inc;

    reg  [15:0] triangle_sample = 16'd0;
    reg         triangle_up = 1'b1;
    reg  [31:0] triangle_word_r = 32'd0;
    reg  [23:0] sine_phase = 24'd0;
    reg  [31:0] sine_word_r = 32'd0;

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

    function [15:0] sine_quarter;
        input [5:0] index;
        begin
            case (index)
            6'd0: sine_quarter = 16'd0;
            6'd1: sine_quarter = 16'd817;
            6'd2: sine_quarter = 16'd1633;
            6'd3: sine_quarter = 16'd2449;
            6'd4: sine_quarter = 16'd3263;
            6'd5: sine_quarter = 16'd4074;
            6'd6: sine_quarter = 16'd4884;
            6'd7: sine_quarter = 16'd5690;
            6'd8: sine_quarter = 16'd6493;
            6'd9: sine_quarter = 16'd7291;
            6'd10: sine_quarter = 16'd8085;
            6'd11: sine_quarter = 16'd8875;
            6'd12: sine_quarter = 16'd9658;
            6'd13: sine_quarter = 16'd10436;
            6'd14: sine_quarter = 16'd11207;
            6'd15: sine_quarter = 16'd11971;
            6'd16: sine_quarter = 16'd12728;
            6'd17: sine_quarter = 16'd13477;
            6'd18: sine_quarter = 16'd14217;
            6'd19: sine_quarter = 16'd14949;
            6'd20: sine_quarter = 16'd15671;
            6'd21: sine_quarter = 16'd16383;
            6'd22: sine_quarter = 16'd17086;
            6'd23: sine_quarter = 16'd17778;
            6'd24: sine_quarter = 16'd18458;
            6'd25: sine_quarter = 16'd19128;
            6'd26: sine_quarter = 16'd19785;
            6'd27: sine_quarter = 16'd20430;
            6'd28: sine_quarter = 16'd21062;
            6'd29: sine_quarter = 16'd21681;
            6'd30: sine_quarter = 16'd22287;
            6'd31: sine_quarter = 16'd22879;
            6'd32: sine_quarter = 16'd23457;
            6'd33: sine_quarter = 16'd24020;
            6'd34: sine_quarter = 16'd24568;
            6'd35: sine_quarter = 16'd25101;
            6'd36: sine_quarter = 16'd25618;
            6'd37: sine_quarter = 16'd26120;
            6'd38: sine_quarter = 16'd26605;
            6'd39: sine_quarter = 16'd27073;
            6'd40: sine_quarter = 16'd27525;
            6'd41: sine_quarter = 16'd27960;
            6'd42: sine_quarter = 16'd28377;
            6'd43: sine_quarter = 16'd28777;
            6'd44: sine_quarter = 16'd29158;
            6'd45: sine_quarter = 16'd29522;
            6'd46: sine_quarter = 16'd29867;
            6'd47: sine_quarter = 16'd30194;
            6'd48: sine_quarter = 16'd30502;
            6'd49: sine_quarter = 16'd30791;
            6'd50: sine_quarter = 16'd31061;
            6'd51: sine_quarter = 16'd31311;
            6'd52: sine_quarter = 16'd31542;
            6'd53: sine_quarter = 16'd31754;
            6'd54: sine_quarter = 16'd31945;
            6'd55: sine_quarter = 16'd32117;
            6'd56: sine_quarter = 16'd32269;
            6'd57: sine_quarter = 16'd32401;
            6'd58: sine_quarter = 16'd32513;
            6'd59: sine_quarter = 16'd32604;
            6'd60: sine_quarter = 16'd32675;
            6'd61: sine_quarter = 16'd32726;
            6'd62: sine_quarter = 16'd32757;
            6'd63: sine_quarter = 16'd32767;
            default: sine_quarter = 16'd0;
            endcase
        end
    endfunction

    function [15:0] sine_from_phase;
        input [23:0] phase;
        reg [15:0] mag;
        begin
            case (phase[23:22])
            2'b00: begin
                mag = sine_quarter(phase[21:16]);
                sine_from_phase = mag;
            end
            2'b01: begin
                mag = sine_quarter(~phase[21:16]);
                sine_from_phase = mag;
            end
            2'b10: begin
                mag = sine_quarter(phase[21:16]);
                sine_from_phase = -mag;
            end
            default: begin
                mag = sine_quarter(~phase[21:16]);
                sine_from_phase = -mag;
            end
            endcase
        end
    endfunction

    wire [16:0] triangle_next0 = advance_triangle(triangle_sample, triangle_up, step);
    wire [16:0] triangle_next1 = advance_triangle(triangle_next0[15:0], triangle_next0[16], step);
    wire [16:0] triangle_next2 = advance_triangle(triangle_next1[15:0], triangle_next1[16], step);
    wire [16:0] triangle_next3 = advance_triangle(triangle_next2[15:0], triangle_next2[16], step);

    wire [23:0] sine_phase1 = sine_phase + sine_step;
    wire [23:0] sine_phase2 = sine_phase1 + sine_step;
    wire [23:0] sine_phase3 = sine_phase2 + sine_step;
    wire [23:0] sine_phase4 = sine_phase3 + sine_step;
    wire [15:0] sine_sample0 = sine_from_phase(sine_phase);
    wire [15:0] sine_sample1 = sine_from_phase(sine_phase1);
    wire [15:0] sine_sample2 = sine_from_phase(sine_phase2);
    wire [15:0] sine_sample3 = sine_from_phase(sine_phase3);

    always @(posedge jesd_clk) begin
        if (jesd_rst || !enable) begin
            triangle_sample <= 16'd0;
            triangle_up     <= 1'b1;
            triangle_word_r <= 32'd0;
            sine_phase      <= 24'd0;
            sine_word_r     <= 32'd0;
        end else begin
            triangle_word_r <= {triangle_next0[15:0], triangle_sample};
            triangle_sample <= triangle_next3[15:0];
            triangle_up     <= triangle_next3[16];
            sine_word_r     <= {sine_sample1, sine_sample0};
            sine_phase      <= sine_phase4;
        end
    end

    assign triangle_word = triangle_word_r;
    assign sine_word = sine_word_r;

    wire [63:0] sine_quad_word = {
        sine_sample3,
        sine_sample2,
        sine_sample1,
        sine_sample0
    };
    wire [63:0] dac_zero64 = 64'd0;

    // Converter select is diagnostic-only:
    //   0     broadcast the same scalar waveform to all four converters
    //   1..4  drive only converter 0..3 respectively, others at midscale
    //   5..7  broadcast, matching the original bring-up behavior
    wire drive_converter0 = (active_converter == 3'd0) ||
                            (active_converter == 3'd1) ||
                            (active_converter >= 3'd5);
    wire drive_converter1 = (active_converter == 3'd0) ||
                            (active_converter == 3'd2) ||
                            (active_converter >= 3'd5);
    wire drive_converter2 = (active_converter == 3'd0) ||
                            (active_converter == 3'd3) ||
                            (active_converter >= 3'd5);
    wire drive_converter3 = (active_converter == 3'd0) ||
                            (active_converter == 3'd4) ||
                            (active_converter >= 3'd5);

    function [63:0] swap_sample_bytes64;
        input [63:0] value;
        integer j;
        begin
            for (j = 0; j < 4; j = j + 1) begin
                swap_sample_bytes64[(16*j) +: 16] = {
                    value[(16*j) +: 8],
                    value[(16*j + 8) +: 8]
                };
            end
        end
    endfunction

    wire [63:0] program_converter0 = drive_converter0 ? program_word0 : dac_zero64;
    wire [63:0] program_converter1 = drive_converter1 ? program_word1 : dac_zero64;
    wire [63:0] program_converter2 = drive_converter2 ? program_word2 : dac_zero64;
    wire [63:0] program_converter3 = drive_converter3 ? program_word3 : dac_zero64;

    wire [63:0] sine_converter0 = drive_converter0 ? sine_quad_word : dac_zero64;
    wire [63:0] sine_converter1 = drive_converter1 ? sine_quad_word : dac_zero64;
    wire [63:0] sine_converter2 = drive_converter2 ? sine_quad_word : dac_zero64;
    wire [63:0] sine_converter3 = drive_converter3 ? sine_quad_word : dac_zero64;

    wire [63:0] neuron_converter0 = drive_converter0 ? neuron_word0 : dac_zero64;
    wire [63:0] neuron_converter1 = drive_converter1 ? neuron_word1 : dac_zero64;
    wire [63:0] neuron_converter2 = drive_converter2 ? neuron_word2 : dac_zero64;
    wire [63:0] neuron_converter3 = drive_converter3 ? neuron_word3 : dac_zero64;

    wire [1:0] source_mode0 = source_modes[1:0];
    wire [1:0] source_mode1 = source_modes[3:2];
    wire [1:0] source_mode2 = source_modes[5:4];
    wire [1:0] source_mode3 = source_modes[7:6];

    wire [1:0] effective_source_mode0 = (source_mode0 == 2'd0) ?
        (program_enable ? 2'd2 : 2'd1) : source_mode0;
    wire [1:0] effective_source_mode1 = (source_mode1 == 2'd0) ?
        (program_enable ? 2'd2 : 2'd1) : source_mode1;
    wire [1:0] effective_source_mode2 = (source_mode2 == 2'd0) ?
        (program_enable ? 2'd2 : 2'd1) : source_mode2;
    wire [1:0] effective_source_mode3 = (source_mode3 == 2'd0) ?
        (program_enable ? 2'd2 : 2'd1) : source_mode3;

    reg [63:0] src_converter0;
    reg [63:0] src_converter1;
    reg [63:0] src_converter2;
    reg [63:0] src_converter3;

    always @(*) begin
        case (effective_source_mode0)
        2'd2: begin
            src_converter0 = program_converter0;
        end
        2'd3: begin
            src_converter0 = neuron_converter0;
        end
        default: begin
            src_converter0 = sine_converter0;
        end
        endcase

        case (effective_source_mode1)
        2'd2: begin
            src_converter1 = program_converter1;
        end
        2'd3: begin
            src_converter1 = neuron_converter1;
        end
        default: begin
            src_converter1 = sine_converter1;
        end
        endcase

        case (effective_source_mode2)
        2'd2: begin
            src_converter2 = program_converter2;
        end
        2'd3: begin
            src_converter2 = neuron_converter2;
        end
        default: begin
            src_converter2 = sine_converter2;
        end
        endcase

        case (effective_source_mode3)
        2'd2: begin
            src_converter3 = program_converter3;
        end
        2'd3: begin
            src_converter3 = neuron_converter3;
        end
        default: begin
            src_converter3 = sine_converter3;
        end
        endcase
    end

    wire [63:0] preimage_converter0;
    wire [63:0] preimage_converter1;
    wire [63:0] preimage_converter2;
    wire [63:0] preimage_converter3;

    genvar preimage_index;
    generate
        for (preimage_index = 0; preimage_index < 4; preimage_index = preimage_index + 1) begin : gen_dac_sample_preimage
            wire [15:0] desired0 = src_converter0[(16*preimage_index) +: 16];
            wire [15:0] desired1 = src_converter1[(16*preimage_index) +: 16];
            wire [15:0] desired2 = src_converter2[(16*preimage_index) +: 16];
            wire [15:0] desired3 = src_converter3[(16*preimage_index) +: 16];

            assign preimage_converter0[(16*preimage_index) +: 16] = {desired2[7:0], desired2[15:8]};
            assign preimage_converter1[(16*preimage_index) +: 16] = {desired3[7:0], desired3[15:8]};
            assign preimage_converter2[(16*preimage_index) +: 16] = {desired1[15:8], desired0[7:0]};
            assign preimage_converter3[(16*preimage_index) +: 16] = {desired1[7:0], desired0[15:8]};
        end
    endgenerate

    wire [63:0] physical_converter0;
    wire [63:0] physical_converter1;
    wire [63:0] physical_converter2;
    wire [63:0] physical_converter3;

    dac39j84_physical_mapper u_dac39j84_physical_mapper (
        .dac_out0   (src_converter0),
        .dac_out1   (src_converter1),
        .dac_out2   (src_converter2),
        .dac_out3   (src_converter3),
        .converter0 (physical_converter0),
        .converter1 (physical_converter1),
        .converter2 (physical_converter2),
        .converter3 (physical_converter3)
    );

    // sample_map_mode:
    //   0 = native LiteJESD converter buses, diagnostic only
    //   1 = four-channel preimage + legacy DAC39J84 sample remap
    //   2 = direct source buses through legacy DAC39J84 sample remap
    //   3 = physical DAC output mapper, normal four-channel path
    wire use_preimage_remap = (sample_map_mode == 2'd1);
    wire use_any_remap = use_preimage_remap ||
                         (sample_map_mode == 2'd2);
    wire use_physical_map = (sample_map_mode == 2'd3);

    wire [63:0] remap_in0 = use_preimage_remap ? preimage_converter0 : src_converter0;
    wire [63:0] remap_in1 = use_preimage_remap ? preimage_converter1 : src_converter1;
    wire [63:0] remap_in2 = use_preimage_remap ? preimage_converter2 : src_converter2;
    wire [63:0] remap_in3 = use_preimage_remap ? preimage_converter3 : src_converter3;
    wire [63:0] remap_out0;
    wire [63:0] remap_out1;
    wire [63:0] remap_out2;
    wire [63:0] remap_out3;

    dac39j84_sample_remap u_dac39j84_sample_remap (
        .converter0_in  (remap_in0),
        .converter1_in  (remap_in1),
        .converter2_in  (remap_in2),
        .converter3_in  (remap_in3),
        .converter0_out (remap_out0),
        .converter1_out (remap_out1),
        .converter2_out (remap_out2),
        .converter3_out (remap_out3)
    );

    wire [63:0] jesd_converter0 = use_physical_map ? physical_converter0 :
                                   use_any_remap ? remap_out0 : src_converter0;
    wire [63:0] jesd_converter1 = use_physical_map ? physical_converter1 :
                                   use_any_remap ? remap_out1 : src_converter1;
    wire [63:0] jesd_converter2 = use_physical_map ? physical_converter2 :
                                   use_any_remap ? remap_out2 : src_converter2;
    wire [63:0] jesd_converter3 = use_physical_map ? physical_converter3 :
                                   use_any_remap ? remap_out3 : src_converter3;

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
        .converter0       (jesd_converter0),
        .converter1       (jesd_converter1),
        .converter2       (jesd_converter2),
        .converter3       (jesd_converter3),
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
        4'd0,
        effective_source_mode0,
        sample_map_mode,
        active_converter,
        stpl_enable,
        sync_n,
        sysref,
        litejesd_ready,
        enable,
        phy_tx_rst,
        triangle_up,
        jesd_rst,
        sine_step[3:0],
        program_enable,
        1'b0
    };

endmodule

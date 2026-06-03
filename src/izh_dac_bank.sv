`timescale 1ns/1ps

module izh_dac_bank (
    input  wire        clk,
    input  wire        reset,
    input  wire        cfg_strobe,
    input  wire [1:0]  cfg_channel,
    input  wire        cfg_all,
    input  wire [3:0]  cfg_param,
    input  wire [31:0] cfg_value,
    input  wire [2:0]  debug_channel,

    output wire [63:0] dac_word0,
    output wire [63:0] dac_word1,
    output wire [63:0] dac_word2,
    output wire [63:0] dac_word3,
    output wire [7:0]  source_modes,
    output wire [31:0] debug_word,
    output wire [3:0]  spike_flags
);

    // Izhikevich regular-spiking defaults:
    //   a=0.02, b=0.20, c=-65, d=8, I_input=0, I_constant=10.
    localparam signed [31:0] DEFAULT_A      = 32'sh0000_051F; // 0.02
    localparam signed [31:0] DEFAULT_B      = 32'sh0000_3333; // 0.20
    localparam signed [31:0] DEFAULT_C      = 32'shFFBF_0000; // -65 mV
    localparam signed [31:0] DEFAULT_D      = 32'sh0008_0000; // 8
    localparam signed [31:0] DEFAULT_I      = 32'sh0000_0000; // external I input held at 0
    localparam signed [31:0] DEFAULT_DT     = 32'sh0000_1000; // 0.0625
    localparam signed [31:0] DEFAULT_ICONST = 32'sh000A_0000; // constant drive 10
    localparam signed [31:0] DEFAULT_OFFSET = 32'sh0000_0000;

    reg signed [31:0] a_param [0:3];
    reg signed [31:0] b_param [0:3];
    reg signed [31:0] c_param [0:3];
    reg signed [31:0] d_param [0:3];
    reg signed [31:0] i_param [0:3];
    reg signed [31:0] timestep_param [0:3];
    reg signed [31:0] i_constant [0:3];
    reg signed [31:0] v_offset [0:3];
    reg [1:0] source_mode [0:3];
    reg [3:0] neuron_reset = 4'hF;

    wire signed [31:0] v_out [0:3];
    wire signed [31:0] u_out [0:3];
    wire [15:0] dac_sample [0:3];
    wire [63:0] dac_word [0:3];
    wire [3:0] spike;

    integer cfg_i;

    task set_defaults;
        input integer idx;
        begin
            a_param[idx]        <= DEFAULT_A;
            b_param[idx]        <= DEFAULT_B;
            c_param[idx]        <= DEFAULT_C;
            d_param[idx]        <= DEFAULT_D;
            i_param[idx]        <= DEFAULT_I;
            timestep_param[idx] <= DEFAULT_DT;
            i_constant[idx]     <= DEFAULT_ICONST;
            v_offset[idx]       <= DEFAULT_OFFSET;
            source_mode[idx]    <= 2'd0;
        end
    endtask

    task write_param;
        input integer idx;
        input [3:0] param;
        input [31:0] value;
        begin
            case (param)
            4'd0: a_param[idx]        <= value;
            4'd1: b_param[idx]        <= value;
            4'd2: c_param[idx]        <= value;
            4'd3: d_param[idx]        <= value;
            4'd4: i_param[idx]        <= value;
            4'd5: timestep_param[idx] <= value;
            4'd6: i_constant[idx]     <= value;
            4'd7: v_offset[idx]       <= value;
            4'd8: source_mode[idx]    <= value[1:0];
            default: begin end
            endcase
        end
    endtask

    always @(posedge clk) begin
        if (reset) begin
            for (cfg_i = 0; cfg_i < 4; cfg_i = cfg_i + 1) begin
                set_defaults(cfg_i);
            end
            neuron_reset <= 4'hF;
        end else begin
            neuron_reset <= 4'h0;

            if (cfg_strobe) begin
                if (cfg_param == 4'hE) begin
                    if (cfg_all) begin
                        for (cfg_i = 0; cfg_i < 4; cfg_i = cfg_i + 1) begin
                            set_defaults(cfg_i);
                        end
                        neuron_reset <= 4'hF;
                    end else begin
                        set_defaults(cfg_channel);
                        neuron_reset[cfg_channel] <= 1'b1;
                    end
                end else if (cfg_param == 4'hF) begin
                    if (cfg_all) begin
                        neuron_reset <= 4'hF;
                    end else begin
                        neuron_reset[cfg_channel] <= 1'b1;
                    end
                end else begin
                    if (cfg_all) begin
                        for (cfg_i = 0; cfg_i < 4; cfg_i = cfg_i + 1) begin
                            write_param(cfg_i, cfg_param, cfg_value);
                        end
                        neuron_reset <= 4'hF;
                    end else begin
                        write_param(cfg_channel, cfg_param, cfg_value);
                        neuron_reset[cfg_channel] <= 1'b1;
                    end
                end
            end
        end
    end

    genvar neuron_idx;
    generate
        for (neuron_idx = 0; neuron_idx < 4; neuron_idx = neuron_idx + 1) begin : gen_izh_channels
            izh_dac_channel u_izh_dac_channel (
                .clk        (clk),
                .reset      (reset | neuron_reset[neuron_idx]),
                .a_param    (a_param[neuron_idx]),
                .b_param    (b_param[neuron_idx]),
                .c_param    (c_param[neuron_idx]),
                .d_param    (d_param[neuron_idx]),
                .i_param    (i_param[neuron_idx]),
                .v_timestep (timestep_param[neuron_idx]),
                .i_constant (i_constant[neuron_idx]),
                .v_offset   (v_offset[neuron_idx]),
                .spike      (spike[neuron_idx]),
                .v_out      (v_out[neuron_idx]),
                .u_out      (u_out[neuron_idx]),
                .dac_sample (dac_sample[neuron_idx]),
                .dac_word   (dac_word[neuron_idx])
            );
        end
    endgenerate

    assign dac_word0 = dac_word[0];
    assign dac_word1 = dac_word[1];
    assign dac_word2 = dac_word[2];
    assign dac_word3 = dac_word[3];
    assign source_modes = {
        source_mode[3],
        source_mode[2],
        source_mode[1],
        source_mode[0]
    };

    wire [1:0] dbg_idx = (debug_channel >= 3'd1 && debug_channel <= 3'd4) ?
                         (debug_channel[1:0] - 2'd1) : 2'd0;

    assign debug_word = {
        8'h1A,
        source_mode[dbg_idx],
        spike[dbg_idx],
        dbg_idx,
        dac_sample[dbg_idx],
        v_out[dbg_idx][2:0]
    };
    assign spike_flags = spike;

endmodule

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
    localparam [23:0] DEFAULT_UPDATE_PERIOD = 24'd1024;

    reg signed [31:0] a_param [0:3];
    reg signed [31:0] b_param [0:3];
    reg signed [31:0] c_param [0:3];
    reg signed [31:0] d_param [0:3];
    reg signed [31:0] i_param [0:3];
    reg signed [31:0] timestep_param [0:3];
    reg signed [31:0] i_constant [0:3];
    reg signed [31:0] v_offset [0:3];
    reg [23:0] update_period [0:3];
    reg direct_vout_mode [0:3];
    reg [1:0] source_mode [0:3];
    reg [3:0] neuron_reset = 4'hF;
    reg [23:0] spike_counter [0:3];
    reg [23:0] last_spike_interval [0:3];

    wire signed [31:0] v_out [0:3];
    wire signed [31:0] u_out [0:3];
    wire [3:0] spike;
    wire [3:0] step_enable;

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
            update_period[idx]  <= DEFAULT_UPDATE_PERIOD;
            direct_vout_mode[idx] <= 1'b0;
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
            4'd9: update_period[idx]  <= value[23:0];
            4'd10: direct_vout_mode[idx] <= value[0];
            default: begin end
            endcase
        end
    endtask

    always @(posedge clk) begin
        if (reset) begin
            for (cfg_i = 0; cfg_i < 4; cfg_i = cfg_i + 1) begin
                set_defaults(cfg_i);
                spike_counter[cfg_i] <= 24'd0;
                last_spike_interval[cfg_i] <= 24'd0;
            end
            neuron_reset <= 4'hF;
        end else begin
            for (cfg_i = 0; cfg_i < 4; cfg_i = cfg_i + 1) begin
                if (spike[cfg_i]) begin
                    last_spike_interval[cfg_i] <= spike_counter[cfg_i];
                    spike_counter[cfg_i] <= 24'd0;
                end else if (spike_counter[cfg_i] != 24'hFF_FFFF) begin
                    spike_counter[cfg_i] <= spike_counter[cfg_i] + 1'b1;
                end
            end

            neuron_reset <= 4'h0;

            if (cfg_strobe) begin
                if (cfg_param == 4'hE) begin
                    if (cfg_all) begin
                        for (cfg_i = 0; cfg_i < 4; cfg_i = cfg_i + 1) begin
                            set_defaults(cfg_i);
                            spike_counter[cfg_i] <= 24'd0;
                            last_spike_interval[cfg_i] <= 24'd0;
                        end
                        neuron_reset <= 4'hF;
                    end else begin
                        set_defaults(cfg_channel);
                        spike_counter[cfg_channel] <= 24'd0;
                        last_spike_interval[cfg_channel] <= 24'd0;
                        neuron_reset[cfg_channel] <= 1'b1;
                    end
                end else if (cfg_param == 4'hF) begin
                    if (cfg_all) begin
                        neuron_reset <= 4'hF;
                        for (cfg_i = 0; cfg_i < 4; cfg_i = cfg_i + 1) begin
                            spike_counter[cfg_i] <= 24'd0;
                            last_spike_interval[cfg_i] <= 24'd0;
                        end
                    end else begin
                        neuron_reset[cfg_channel] <= 1'b1;
                        spike_counter[cfg_channel] <= 24'd0;
                        last_spike_interval[cfg_channel] <= 24'd0;
                    end
                end else begin
                    if (cfg_all) begin
                        for (cfg_i = 0; cfg_i < 4; cfg_i = cfg_i + 1) begin
                            write_param(cfg_i, cfg_param, cfg_value);
                            spike_counter[cfg_i] <= 24'd0;
                            last_spike_interval[cfg_i] <= 24'd0;
                        end
                        neuron_reset <= 4'hF;
                    end else begin
                        write_param(cfg_channel, cfg_param, cfg_value);
                        neuron_reset[cfg_channel] <= 1'b1;
                        spike_counter[cfg_channel] <= 24'd0;
                        last_spike_interval[cfg_channel] <= 24'd0;
                    end
                end
            end
        end
    end

    genvar neuron_idx;
    generate
        for (neuron_idx = 0; neuron_idx < 4; neuron_idx = neuron_idx + 1) begin : gen_izh_channels
            reg [23:0] update_counter = 24'd0;
            wire [23:0] update_reload = (update_period[neuron_idx] <= 24'd1) ?
                                         24'd0 : (update_period[neuron_idx] - 1'b1);

            always @(posedge clk) begin
                if (reset | neuron_reset[neuron_idx]) begin
                    update_counter <= 24'd0;
                end else if (update_reload == 24'd0) begin
                    update_counter <= 24'd0;
                end else if (update_counter == update_reload) begin
                    update_counter <= 24'd0;
                end else begin
                    update_counter <= update_counter + 1'b1;
                end
            end

            assign step_enable[neuron_idx] = (update_counter == 24'd0);

            izh_neuron u_izh_neuron (
                .clk        (clk),
                .reset      (reset | neuron_reset[neuron_idx]),
                .a_param    (a_param[neuron_idx]),
                .b_param    (b_param[neuron_idx]),
                .c_param    (c_param[neuron_idx]),
                .d_param    (d_param[neuron_idx]),
                .I          (i_param[neuron_idx]),
                .v_timestep (timestep_param[neuron_idx]),
                .I_constant (i_constant[neuron_idx]),
                .step_enable(step_enable[neuron_idx]),
                .SPIKE      (spike[neuron_idx]),
                .v_out      (v_out[neuron_idx]),
                .u_out      (u_out[neuron_idx])
            );
        end
    endgenerate

    assign source_modes = {
        source_mode[3],
        source_mode[2],
        source_mode[1],
        source_mode[0]
    };

    wire [1:0] dbg_idx = (debug_channel >= 3'd1 && debug_channel <= 3'd4) ?
                         (debug_channel[1:0] - 2'd1) : 2'd0;

    reg [31:0] debug_word_r;
    always @(*) begin
        case (debug_channel)
        3'd5: begin
            debug_word_r = {8'h1D, timestep_param[0][23:0]};
        end
        3'd6: begin
            debug_word_r = {8'h1E, last_spike_interval[0]};
        end
        3'd7: begin
            debug_word_r = {8'h1F, update_period[0]};
        end
        default: begin
            debug_word_r = {
                8'h1A,
                direct_vout_mode[dbg_idx],
                source_mode[dbg_idx],
                spike[dbg_idx],
                dbg_idx,
                last_spike_interval[dbg_idx][17:0]
            };
        end
        endcase
    end

    assign debug_word = debug_word_r;
    assign spike_flags = spike;

endmodule

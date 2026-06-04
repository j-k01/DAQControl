`timescale 1ns/1ps

module izh_dac_integration_tb;
    reg clk = 1'b0;
    always #5 clk = ~clk;

    task tick;
        begin
            @(posedge clk);
            #1;
        end
    endtask

    task check16;
        input [255:0] label;
        input [15:0] actual;
        input [15:0] expected;
        begin
            if (actual !== expected) begin
                $display("FAIL %0s actual=0x%04x expected=0x%04x",
                         label, actual, expected);
                $fatal;
            end
        end
    endtask

    task check32;
        input [255:0] label;
        input [31:0] actual;
        input [31:0] expected;
        begin
            if (actual !== expected) begin
                $display("FAIL %0s actual=0x%08x expected=0x%08x",
                         label, actual, expected);
                $fatal;
            end
        end
    endtask

    task check64;
        input [255:0] label;
        input [63:0] actual;
        input [63:0] expected;
        begin
            if (actual !== expected) begin
                $display("FAIL %0s actual=0x%016x expected=0x%016x",
                         label, actual, expected);
                $fatal;
            end
        end
    endtask

    reg signed [31:0] ch_a = 32'sh0000_051F;
    reg signed [31:0] ch_b = 32'sh0000_3333;
    reg signed [31:0] ch_c = 32'shFFBF_0000;
    reg signed [31:0] ch_d = 32'sh0008_0000;
    reg signed [31:0] ch_i = 32'sh0000_0000;
    reg signed [31:0] ch_dt = 32'sh0000_1000;
    reg signed [31:0] ch_iconst = 32'sh000A_0000;
    reg signed [31:0] ch_offset = 32'sh0000_0000;
    reg [23:0] ch_update_period = 24'd1;
    reg ch_reset = 1'b1;
    wire ch_spike;
    wire signed [31:0] ch_v;
    wire signed [31:0] ch_u;
    wire [15:0] ch_dac_sample;
    wire [63:0] ch_dac_word;

    izh_dac_channel u_channel (
        .clk        (clk),
        .reset      (ch_reset),
        .a_param    (ch_a),
        .b_param    (ch_b),
        .c_param    (ch_c),
        .d_param    (ch_d),
        .i_param    (ch_i),
        .v_timestep (ch_dt),
        .i_constant (ch_iconst),
        .v_offset   (ch_offset),
        .update_period(ch_update_period),
        .spike      (ch_spike),
        .v_out      (ch_v),
        .u_out      (ch_u),
        .dac_sample (ch_dac_sample),
        .dac_word   (ch_dac_word)
    );

    reg        bank_reset = 1'b1;
    reg        cfg_strobe = 1'b0;
    reg [1:0]  cfg_channel = 2'd0;
    reg        cfg_all = 1'b0;
    reg [3:0]  cfg_param = 4'd0;
    reg [31:0] cfg_value = 32'd0;
    reg [2:0]  debug_channel = 3'd1;
    wire [63:0] bank_word0;
    wire [63:0] bank_word1;
    wire [63:0] bank_word2;
    wire [63:0] bank_word3;
    wire [7:0]  bank_source_modes;
    wire [31:0] bank_debug_word;
    wire [3:0]  bank_spike_flags;

    izh_dac_bank u_bank (
        .clk           (clk),
        .reset         (bank_reset),
        .cfg_strobe    (cfg_strobe),
        .cfg_channel   (cfg_channel),
        .cfg_all       (cfg_all),
        .cfg_param     (cfg_param),
        .cfg_value     (cfg_value),
        .debug_channel (debug_channel),
        .dac_word0     (bank_word0),
        .dac_word1     (bank_word1),
        .dac_word2     (bank_word2),
        .dac_word3     (bank_word3),
        .source_modes  (bank_source_modes),
        .debug_word    (bank_debug_word),
        .spike_flags   (bank_spike_flags)
    );

    task pulse_cfg;
        input [1:0] channel;
        input all_channels;
        input [3:0] param;
        input [31:0] value;
        begin
            cfg_channel = channel;
            cfg_all = all_channels;
            cfg_param = param;
            cfg_value = value;
            cfg_strobe = 1'b1;
            tick();
            cfg_strobe = 1'b0;
            tick();
        end
    endtask

    initial begin
        repeat (3) tick();
        ch_reset = 1'b0;
        bank_reset = 1'b0;
        repeat (2) tick();

        force u_channel.neuron_spike = 1'b0;
        force u_channel.neuron_v = 32'shFFB0_0000;
        #1;
        check16("idle spike sample", ch_dac_sample, 16'h0000);
        check64("idle spike word", ch_dac_word, 64'h0000_0000_0000_0000);

        force u_channel.neuron_spike = 1'b1;
        tick();
        check16("spike trapezoid first sample", ch_dac_sample, 16'h1800);
        check64("spike trapezoid first beat", ch_dac_word, 64'h6000_6000_3000_1800);

        force u_channel.neuron_spike = 1'b0;
        tick();
        check64("spike trapezoid second beat", ch_dac_word, 64'h0000_1800_3000_6000);

        tick();
        check64("spike trapezoid ends", ch_dac_word, 64'h0000_0000_0000_0000);

        force u_channel.neuron_spike = 1'b1;
        tick();
        check64("spike trapezoid restart first beat", ch_dac_word, 64'h6000_6000_3000_1800);

        force u_channel.neuron_spike = 1'b0;
        tick();
        force u_channel.neuron_spike = 1'b1;
        tick();
        check64("mid-pulse spike restarts trapezoid", ch_dac_word, 64'h6000_6000_3000_1800);

        release u_channel.neuron_v;
        release u_channel.neuron_spike;

        check32("bank default source modes", {24'd0, bank_source_modes}, 32'h0000_0000);

        pulse_cfg(2'd2, 1'b0, 4'd8, 32'd3);
        check32("ch2 source mode izh", {24'd0, bank_source_modes}, 32'h0000_0030);

        pulse_cfg(2'd1, 1'b0, 4'd4, 32'h0010_0000);
        check32("ch1 current", u_bank.i_param[1], 32'h0010_0000);

        pulse_cfg(2'd3, 1'b0, 4'd7, 32'h0001_0000);
        check32("ch3 offset", u_bank.v_offset[3], 32'h0001_0000);

        pulse_cfg(2'd0, 1'b0, 4'd9, 32'd64);
        check32("ch0 update period", {8'd0, u_bank.update_period[0]}, 32'h0000_0040);

        pulse_cfg(2'd0, 1'b1, 4'd8, 32'd1);
        check32("all source mode dds", {24'd0, bank_source_modes}, 32'h0000_0055);

        pulse_cfg(2'd0, 1'b1, 4'hE, 32'd0);
        check32("defaults clear source modes", {24'd0, bank_source_modes}, 32'h0000_0000);
        check32("defaults restore a", u_bank.a_param[0], 32'h0000_051F);
        check32("defaults restore zero external current", u_bank.i_param[0], 32'h0000_0000);
        check32("defaults restore iconst", u_bank.i_constant[0], 32'h000A_0000);
        check32("defaults restore update period", {8'd0, u_bank.update_period[0]}, 32'h0000_0400);

        debug_channel = 3'd1;
        #1;
        if (bank_debug_word[31:24] !== 8'h1A) begin
            $display("FAIL debug marker actual=0x%08x", bank_debug_word);
            $fatal;
        end
        debug_channel = 3'd5;
        #1;
        check32("dt debug word", bank_debug_word, 32'h1D00_1000);
        debug_channel = 3'd6;
        #1;
        if (bank_debug_word[31:24] !== 8'h1E) begin
            $display("FAIL interval debug marker actual=0x%08x", bank_debug_word);
            $fatal;
        end
        debug_channel = 3'd7;
        #1;
        check32("period debug word", bank_debug_word, 32'h1F00_0400);

        $display("IZH DAC integration tests passed.");
        $finish;
    end
endmodule

`timescale 1ns/1ps

// Integrated pulse-shape test:
//   AXI-style 32-bit writes -> spike_shape_bram_bank -> izh_spike_shaper.
//
// This checks the real packing and read-side width used by the board design:
// two signed s16 samples per AXI word, four signed s16 samples per DAC beat.
module spike_shape_bram_integration_tb;
    localparam integer AW          = 10;
    localparam integer MAX_BEATS   = (1 << AW);
    localparam integer MAX_SAMPLES = MAX_BEATS * 4;
    localparam integer MAX_OBS     = 256;

    reg axi_clk = 1'b0;
    reg dac_clk = 1'b0;
    reg reset = 1'b1;
    always #5 axi_clk = ~axi_clk;
    always #2 dac_clk = ~dac_clk;

    reg  [31:0] axi_addr = 32'd0;
    reg  [31:0] axi_din = 32'd0;
    wire [31:0] axi_dout;
    reg         axi_en = 1'b0;
    reg  [3:0]  axi_we = 4'd0;

    wire [AW-1:0] shape_addr0, shape_addr1, shape_addr2, shape_addr3;
    wire [63:0]   shape_data0, shape_data1, shape_data2, shape_data3;
    reg  [AW:0]   nbeats = 11'd1;
    reg  [3:0]    spike = 4'd0;
    wire [3:0]    active;
    wire [63:0]   dac_word0, dac_word1, dac_word2, dac_word3;

    reg [15:0] model [0:MAX_SAMPLES-1];
    reg [63:0] obs0 [0:MAX_OBS-1];
    reg [63:0] obs1 [0:MAX_OBS-1];
    integer errors = 0;

    spike_shape_bram_bank #(
        .ADDR_W (AW)
    ) u_shape_bank (
        .axi_addr     (axi_addr),
        .axi_clk      (axi_clk),
        .axi_din      (axi_din),
        .axi_dout     (axi_dout),
        .axi_en       (axi_en),
        .axi_we       (axi_we),
        .fabric_clk   (dac_clk),
        .fabric_addr0 (shape_addr0),
        .fabric_dout0 (shape_data0),
        .fabric_addr1 (shape_addr1),
        .fabric_dout1 (shape_data1),
        .fabric_addr2 (shape_addr2),
        .fabric_dout2 (shape_data2),
        .fabric_addr3 (shape_addr3),
        .fabric_dout3 (shape_data3)
    );

    izh_spike_shaper #(.ADDR_W(AW)) u_shaper0 (
        .clk(dac_clk), .reset(reset), .spike(spike[0]),
        .shape_addr(shape_addr0), .shape_data(shape_data0),
        .nbeats(nbeats), .active(active[0]), .dac_word(dac_word0)
    );
    izh_spike_shaper #(.ADDR_W(AW)) u_shaper1 (
        .clk(dac_clk), .reset(reset), .spike(spike[1]),
        .shape_addr(shape_addr1), .shape_data(shape_data1),
        .nbeats(nbeats), .active(active[1]), .dac_word(dac_word1)
    );
    izh_spike_shaper #(.ADDR_W(AW)) u_shaper2 (
        .clk(dac_clk), .reset(reset), .spike(spike[2]),
        .shape_addr(shape_addr2), .shape_data(shape_data2),
        .nbeats(nbeats), .active(active[2]), .dac_word(dac_word2)
    );
    izh_spike_shaper #(.ADDR_W(AW)) u_shaper3 (
        .clk(dac_clk), .reset(reset), .spike(spike[3]),
        .shape_addr(shape_addr3), .shape_data(shape_data3),
        .nbeats(nbeats), .active(active[3]), .dac_word(dac_word3)
    );

    function signed [15:0] tone_sample(input integer idx);
        integer phase;
        integer base;
        integer value;
        begin
            phase = idx & 15;
            case (phase)
                0:  base =      0;
                1:  base =   6270;
                2:  base =  11585;
                3:  base =  15137;
                4:  base =  16384;
                5:  base =  15137;
                6:  base =  11585;
                7:  base =   6270;
                8:  base =      0;
                9:  base =  -6270;
                10: base = -11585;
                11: base = -15137;
                12: base = -16384;
                13: base = -15137;
                14: base = -11585;
                default: base = -6270;
            endcase
            value = base + (idx * 23) - 800;
            if (value > 32767) value = 32767;
            if (value < -32768) value = -32768;
            tone_sample = value;
        end
    endfunction

    function [31:0] pack2(input [15:0] s0, input [15:0] s1);
        begin
            pack2 = {s1, s0};
        end
    endfunction

    function [63:0] expected_beat(input integer beat);
        integer sidx;
        begin
            sidx = beat * 4;
            expected_beat = {model[sidx + 3], model[sidx + 2],
                             model[sidx + 1], model[sidx + 0]};
        end
    endfunction

    function integer beat_count_for_samples(input integer sample_count);
        begin
            beat_count_for_samples = (sample_count + 3) / 4;
            if (beat_count_for_samples == 0)
                beat_count_for_samples = 1;
        end
    endfunction

    task axi_write_word(input integer word_addr, input [31:0] data);
        begin
            @(negedge axi_clk);
            axi_addr = word_addr << 2;
            axi_din  = data;
            axi_en   = 1'b1;
            axi_we   = 4'hF;
            @(negedge axi_clk);
            axi_en   = 1'b0;
            axi_we   = 4'h0;
            axi_addr = 32'd0;
            axi_din  = 32'd0;
        end
    endtask

    task program_shape(input integer sample_count);
        integer i;
        integer sidx;
        integer beats;
        integer total_words;
        begin
            for (i = 0; i < MAX_SAMPLES; i = i + 1)
                model[i] = 16'd0;
            for (i = 0; i < sample_count; i = i + 1)
                model[i] = tone_sample(i);

            beats = beat_count_for_samples(sample_count);
            total_words = beats * 2;
            for (i = 0; i < total_words; i = i + 1) begin
                sidx = i * 2;
                axi_write_word(i, pack2(model[sidx], model[sidx + 1]));
            end

            nbeats = beats;
            repeat (6) @(negedge dac_clk);
        end
    endtask

    function integer find_sequence0(input integer beats, input integer start_at, input integer limit);
        integer off;
        integer b;
        reg ok;
        begin
            find_sequence0 = -1;
            for (off = start_at; (off <= limit - beats) && (find_sequence0 < 0); off = off + 1) begin
                ok = 1'b1;
                for (b = 0; b < beats; b = b + 1)
                    if (obs0[off + b] !== expected_beat(b))
                        ok = 1'b0;
                if (ok)
                    find_sequence0 = off;
            end
        end
    endfunction

    function integer find_sequence1(input integer beats, input integer start_at, input integer limit);
        integer off;
        integer b;
        reg ok;
        begin
            find_sequence1 = -1;
            for (off = start_at; (off <= limit - beats) && (find_sequence1 < 0); off = off + 1) begin
                ok = 1'b1;
                for (b = 0; b < beats; b = b + 1)
                    if (obs1[off + b] !== expected_beat(b))
                        ok = 1'b0;
                if (ok)
                    find_sequence1 = off;
            end
        end
    endfunction

    task assert_single_pulse(input integer sample_count, input string label);
        integer beats;
        integer limit;
        integer i;
        integer match;
        begin
            beats = beat_count_for_samples(sample_count);
            limit = beats + 8;
            for (i = 0; i < MAX_OBS; i = i + 1)
                obs0[i] = 64'hx;

            @(negedge dac_clk);
            spike[0] = 1'b1;
            for (i = 0; i < limit; i = i + 1) begin
                @(negedge dac_clk);
                if (i == 0)
                    spike[0] = 1'b0;
                obs0[i] = dac_word0;
            end

            match = find_sequence0(beats, 0, limit);
            if (match < 0) begin
                errors = errors + 1;
                $display("FAIL %s: did not find %0d-beat pulse", label, beats);
            end else begin
                if (obs0[match + beats] !== 64'd0) begin
                    errors = errors + 1;
                    $display("FAIL %s: pulse leaked after nbeats, got %h",
                             label, obs0[match + beats]);
                end
                $display("  %s: OK samples=%0d nbeats=%0d offset=%0d",
                         label, sample_count, beats, match);
            end
        end
    endtask

    task assert_restart(input integer sample_count);
        integer beats;
        integer limit;
        integer i;
        integer initial_match;
        integer restart_match;
        begin
            beats = beat_count_for_samples(sample_count);
            limit = beats + 24;
            for (i = 0; i < MAX_OBS; i = i + 1)
                obs0[i] = 64'hx;

            @(negedge dac_clk);
            spike[0] = 1'b1;
            for (i = 0; i < limit; i = i + 1) begin
                @(negedge dac_clk);
                if (i == 0)
                    spike[0] = 1'b0;
                if (i == 8)
                    spike[0] = 1'b1;
                if (i == 9)
                    spike[0] = 1'b0;
                obs0[i] = dac_word0;
            end

            initial_match = find_sequence0(4, 0, 12);
            restart_match = find_sequence0(beats, 8, limit);
            if (initial_match < 0 || restart_match < 0) begin
                errors = errors + 1;
                $display("FAIL restart: first_match=%0d restart_match=%0d",
                         initial_match, restart_match);
            end else if (obs0[restart_match + beats] !== 64'd0) begin
                errors = errors + 1;
                $display("FAIL restart: restarted pulse leaked after nbeats");
            end else begin
                $display("  restart: OK first_offset=%0d restart_offset=%0d",
                         initial_match, restart_match);
            end
        end
    endtask

    task assert_staggered_two_channel(input integer sample_count);
        integer beats;
        integer limit;
        integer i;
        integer match0;
        integer match1;
        begin
            beats = beat_count_for_samples(sample_count);
            limit = beats + 20;
            for (i = 0; i < MAX_OBS; i = i + 1) begin
                obs0[i] = 64'hx;
                obs1[i] = 64'hx;
            end

            @(negedge dac_clk);
            spike[0] = 1'b1;
            for (i = 0; i < limit; i = i + 1) begin
                @(negedge dac_clk);
                if (i == 0)
                    spike[0] = 1'b0;
                if (i == 6)
                    spike[1] = 1'b1;
                if (i == 7)
                    spike[1] = 1'b0;
                obs0[i] = dac_word0;
                obs1[i] = dac_word1;
            end

            match0 = find_sequence0(beats, 0, limit);
            match1 = find_sequence1(beats, 0, limit);
            if (match0 < 0 || match1 < 0 || match1 <= match0) begin
                errors = errors + 1;
                $display("FAIL staggered: match0=%0d match1=%0d", match0, match1);
            end else begin
                $display("  staggered readers: OK ch0_offset=%0d ch1_offset=%0d",
                         match0, match1);
            end
        end
    endtask

    integer i;
    initial begin
        for (i = 0; i < MAX_SAMPLES; i = i + 1)
            model[i] = 16'd0;
        for (i = 0; i < MAX_OBS; i = i + 1) begin
            obs0[i] = 64'd0;
            obs1[i] = 64'd0;
        end

        repeat (12) @(negedge dac_clk);
        reset = 1'b0;
        repeat (4) @(negedge dac_clk);

        program_shape(160);
        assert_single_pulse(160, "160-sample programmed tone");
        assert_staggered_two_channel(160);
        assert_restart(160);

        program_shape(70);
        assert_single_pulse(70, "70-sample reprogrammed tone");

        if (errors == 0)
            $display("TB_RESULT: PASS spike_shape_bram_integration (BRAM program + shaper)");
        else
            $display("TB_RESULT: FAIL spike_shape_bram_integration errors=%0d", errors);
        $finish;
    end

    initial begin
        #200000;
        $display("TB_RESULT: FAIL spike_shape_bram_integration TIMEOUT");
        $finish;
    end
endmodule

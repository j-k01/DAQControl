`timescale 1ns/1ps

// Integrated observation-path TB:
//   clk_50 current/spike packet -> async FIFO CDC -> DAC-domain current words
//   plus spike trigger into the programmable spike shaper.
//
// Verifies:
//   - pure injected-current DAC view is gain-scaled after the CDC
//   - per-neuron monitor words stay unscaled
//   - positive saturation on the DAC-only gain path
//   - the same packet's spike trigger plays the programmed pulse shape while the
//     corresponding current/monitor words are visible in the DAC domain.

module izh_observation_cdc_tb;
    reg src_clk = 1'b0;
    reg dst_clk = 1'b0;
    always #10 src_clk = ~src_clk;   // 50 MHz neuron domain
    always #2  dst_clk = ~dst_clk;   // 250 MHz DAC/JESD domain

    reg src_rst = 1'b1;
    reg signed [31:0] pure_current_q16 = 32'sd0;
    reg [127:0] monitor_q16 = 128'd0;
    reg [3:0] spike_flags = 4'd0;
    reg capture = 1'b0;
    reg cycle_start = 1'b0;
    reg [15:0] pure_gain_q8_8 = 16'h0000; // 0 means hardware default 1.0x

    wire [255:0] mon_words;
    wire [63:0] current_word;
    wire [3:0] spike_start;
    wire cycle_start_dst;

    izh_observation_cdc #(.SHIFT(8)) dut (
        .src_clk(src_clk),
        .src_rst(src_rst),
        .pure_current_q16(pure_current_q16),
        .monitor_q16(monitor_q16),
        .spike_flags(spike_flags),
        .capture(capture),
        .cycle_start(cycle_start),
        .dst_clk(dst_clk),
        .pure_gain_q8_8(pure_gain_q8_8),
        .mon_words(mon_words),
        .current_word(current_word),
        .spike_start(spike_start),
        .cycle_start_dst(cycle_start_dst)
    );

    // Programmed spike shape, modeled as a synchronous BRAM read port.
    wire [3:0] shape_addr;
    reg  [63:0] shape_mem [0:15];
    reg  [63:0] shape_data = 64'd0;
    wire [63:0] spike_word;
    wire        spike_active;

    always @(posedge dst_clk) begin
        shape_data <= shape_mem[shape_addr];
    end

    izh_spike_shaper #(.ADDR_W(4)) u_shape (
        .clk(dst_clk),
        .reset(1'b0),
        .spike(spike_start[0]),
        .shape_addr(shape_addr),
        .shape_data(shape_data),
        .nbeats(5'd3),
        .active(spike_active),
        .dac_word(spike_word)
    );

    function [31:0] q16;
        input integer whole;
        begin
            q16 = whole * 32'sd65536;
        end
    endfunction

    task set_monitors;
        input signed [31:0] m0;
        input signed [31:0] m1;
        input signed [31:0] m2;
        input signed [31:0] m3;
        begin
            monitor_q16 = {m3, m2, m1, m0};
        end
    endtask

    integer errors = 0;

    task send_and_expect;
        input signed [31:0] pure_q16;
        input signed [31:0] mon0_q16;
        input [15:0] expected_current_s16;
        input [15:0] expected_mon0_s16;
        input [8*40-1:0] label;
        integer timeout;
        begin
            @(negedge src_clk);
            pure_current_q16 = pure_q16;
            set_monitors(mon0_q16, mon0_q16 + q16(1), mon0_q16 + q16(2),
                         mon0_q16 + q16(3));
            capture = 1'b1;
            cycle_start = 1'b1;
            spike_flags = 4'b0001;
            @(negedge src_clk);
            capture = 1'b0;
            cycle_start = 1'b0;
            spike_flags = 4'd0;

            timeout = 0;
            while ((spike_start[0] !== 1'b1) && (timeout < 2000)) begin
                @(posedge dst_clk);
                timeout = timeout + 1;
            end
            if (timeout >= 2000) begin
                errors = errors + 1;
                $display("FAIL %0s: no spike_start", label);
            end else begin
                // The cycle marker must be asserted on the exact destination
                // clock that presents the packet's current word.
                @(posedge dst_clk);
                if (cycle_start_dst !== 1'b1 ||
                    current_word !== {expected_current_s16, expected_current_s16,
                                     expected_current_s16, expected_current_s16}) begin
                    errors = errors + 1;
                    $display("FAIL %0s: cycle marker/current misaligned marker=%b current=%h",
                             label, cycle_start_dst, current_word);
                end
                // The shaper's synchronous RAM emits the first visible beat two
                // DAC clocks after spike_start is registered.
                @(posedge dst_clk);
                if (spike_word !== shape_mem[0]) begin
                    errors = errors + 1;
                    $display("FAIL %0s: spike shape beat0 got %h expected %h",
                             label, spike_word, shape_mem[0]);
                end
                if (current_word !== {expected_current_s16, expected_current_s16,
                                     expected_current_s16, expected_current_s16}) begin
                    errors = errors + 1;
                    $display("FAIL %0s: current_word got %h expected sample %h",
                             label, current_word, expected_current_s16);
                end
                if (mon_words[63:0] !== {expected_mon0_s16, expected_mon0_s16,
                                         expected_mon0_s16, expected_mon0_s16}) begin
                    errors = errors + 1;
                    $display("FAIL %0s: mon0 got %h expected sample %h",
                             label, mon_words[63:0], expected_mon0_s16);
                end
            end
        end
    endtask

    integer i;
    initial begin
        for (i = 0; i < 16; i = i + 1)
            shape_mem[i] = {16'h4000 + i[15:0], 16'h3000 + i[15:0],
                            16'h2000 + i[15:0], 16'h1000 + i[15:0]};

        repeat (20) @(posedge src_clk);
        src_rst = 1'b0;
        wait (dut.fifo_wr_rst_busy === 1'b0 && dut.fifo_rd_rst_busy === 1'b0);
        repeat (10) @(posedge dst_clk);

        // 1.0 current, default gain -> 0x0100 after Q16.16 >> 8.
        pure_gain_q8_8 = 16'h0000;
        send_and_expect(q16(1), q16(11), 16'h0100, 16'h0B00, "gain default");

        // DAC-only current gain x20.0 -> 0x1400, monitor remains unscaled.
        pure_gain_q8_8 = 16'h1400;
        send_and_expect(q16(1), q16(11), 16'h1400, 16'h0B00, "gain x20");

        // Large input with x20 saturates only the pure-current DAC view.
        pure_gain_q8_8 = 16'h1400;
        send_and_expect(q16(200), q16(12), 16'h7FFF, 16'h0C00, "gain sat");

        if (errors == 0)
            $display("TB_RESULT: PASS izh_observation_cdc (packet/gain/shape checks)");
        else
            $display("TB_RESULT: FAIL izh_observation_cdc errors=%0d", errors);
        $finish;
    end

    initial begin
        #1000000;
        $display("TB_RESULT: FAIL izh_observation_cdc TIMEOUT");
        $finish;
    end
endmodule

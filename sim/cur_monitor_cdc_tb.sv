`timescale 1ns/1ps

// Self-checking TB for the current-monitor CDC sample-and-hold.  Verifies:
// Q16.16->s16 scaling with positive saturation, negative saturation, and a
// small unsaturated negative value; x4 replication across the beat; and that
// the output HOLDS between capture pulses (only a capture updates it).
//
// Two asynchronous clocks: src = 50 MHz (neuron domain), dst = 250 MHz (DAC).

module cur_monitor_cdc_tb;
    localparam integer N = 4, SHIFT = 8;

    reg              src_clk = 0, dst_clk = 0, src_rst = 1, capture = 0;
    reg  [N*32-1:0]  i_mon = 0;
    wire [N*64-1:0]  mon_words;
    integer          errors = 0;

    cur_monitor_cdc #(.N(N), .SHIFT(SHIFT)) dut (
        .src_clk   (src_clk),
        .src_rst   (src_rst),
        .i_mon     (i_mon),
        .capture   (capture),
        .dst_clk   (dst_clk),
        .mon_words (mon_words)
    );

    always #10 src_clk = ~src_clk;   // 50 MHz
    always #2  dst_clk = ~dst_clk;   // 250 MHz

    // Reference scale (must match the DUT): Q16.16 -> s16, >>SHIFT, saturating.
    function [15:0] scale(input [31:0] q);
        integer v;
        begin
            v = $signed(q) >>> SHIFT;
            if (v > 32767)        scale = 16'h7FFF;
            else if (v < -32768)  scale = 16'h8000;
            else                  scale = v[15:0];
        end
    endfunction

    task expect_word(input integer n, input [31:0] q, input integer tag);
        reg [63:0] got, exp;
        begin
            got = mon_words[n*64 +: 64];
            exp = {scale(q), scale(q), scale(q), scale(q)};
            if (got !== exp) begin
                errors = errors + 1;
                $display("FAIL: tag=%0d n=%0d q=%h got=%h exp=%h", tag, n, q, got, exp);
            end
        end
    endtask

    // One-src-cycle capture pulse.
    task do_capture;
        begin
            @(negedge src_clk); capture = 1;
            @(negedge src_clk); capture = 0;
        end
    endtask

    reg [31:0] va, vb, vc, vd;
    initial begin
        repeat (20) @(negedge src_clk);
        src_rst = 0;
        wait (dut.fifo_wr_rst_busy === 1'b0 && dut.fifo_rd_rst_busy === 1'b0);
        repeat (10) @(posedge dst_clk);

        // --- vector 1: normal values + both saturation rails ---
        va = 32'h000A_0000;   // 10.0  -> >>8 = 2560
        vb = 32'h0001_0000;   //  1.0  -> >>8 = 256
        vc = 32'h7FFF_FFFF;   // large + -> saturate +32767
        vd = 32'h8000_0000;   // large - -> saturate -32768
        i_mon = {vd, vc, vb, va};
        do_capture;
        repeat (12) @(posedge dst_clk);
        expect_word(0, va, 1);
        expect_word(1, vb, 1);
        expect_word(2, vc, 1);
        expect_word(3, vd, 1);

        // --- hold: change inputs WITHOUT a capture; output must not move ---
        i_mon = {32'h0, 32'h0, 32'h0, 32'h0};
        repeat (12) @(posedge dst_clk);
        expect_word(0, va, 2);
        expect_word(3, vd, 2);

        // --- vector 2: a capture now latches the new (zero) inputs ---
        do_capture;
        repeat (12) @(posedge dst_clk);
        expect_word(0, 32'h0, 3);
        expect_word(3, 32'h0, 3);

        // --- vector 3: small negative, no saturation (-256 -> -1) ---
        va = 32'hFFFF_FF00;
        i_mon = {32'h0, 32'h0, 32'h0, va};
        do_capture;
        repeat (12) @(posedge dst_clk);
        expect_word(0, va, 4);

        if (errors == 0)
            $display("TB_RESULT: PASS cur_monitor_cdc (all checks)");
        else
            $display("TB_RESULT: FAIL cur_monitor_cdc errors=%0d", errors);
        $finish;
    end

    // Safety net.
    initial begin
        #50000;
        $display("TB_RESULT: FAIL cur_monitor_cdc TIMEOUT");
        $finish;
    end
endmodule

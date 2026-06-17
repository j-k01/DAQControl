`timescale 1ns/1ps

// Integration TB reproducing the hardware current-monitor datapath end to end,
// minus the crossbar/JESD/DAC/ADC:
//
//   izh_current_player -> i_external -> izh_dac_bank.i_mon
//       -> cur_monitor_cdc -> mon_words
//       -> cur_monitor_cdc(N=1) -> pure current-source DAC word
//
// using the SAME capture logic as top.v (sample_tick | clk_50 heartbeat) and the
// player driven exactly like firmware "CURP 1 49 0x500000" (cps=1 -> sample_tick
// every cycle).  On hardware mon_words came out ~0 (silent); this checks whether
// the silence reproduces in simulation (logic bug) or not (points to timing/ILA).
//
// izh_dac_bank uses reset defaults: i_param=0, i_const=10.0, so
//   i_mon[ch0] = i_external + 10.0  (a swinging triangle when the player runs).

module izh_monitor_integ_tb;
    reg clk_50 = 0, gt_clk = 0;
    always #10 clk_50 = ~clk_50;   // 50 MHz neuron domain
    always #2  gt_clk  = ~gt_clk;  // 250 MHz DAC domain

    reg neuron_rst = 1;

    // ---- current player + behavioral cur_wave BRAM (1-cycle read latency) ----
    reg  [15:0] cps = 1;
    reg  [9:0]  last_index = 49;
    reg         run = 0, restart = 0;
    wire [9:0]  bram_addr;
    wire        bram_en;
    reg  [31:0] bram_data = 0;
    wire [31:0] i_current;
    wire        sample_tick, running;

    reg [31:0] cur_wave [0:1023];
    always @(posedge clk_50) bram_data <= cur_wave[bram_addr];

    izh_current_player #(.ADDR_W(10), .DATA_W(32)) u_player (
        .clk(clk_50), .reset(neuron_rst), .run(run), .restart(restart),
        .hold_last(1'b0),
        .cycles_per_sample(cps), .last_index(last_index),
        .bram_addr(bram_addr), .bram_en(bram_en), .bram_data(bram_data),
        .i_current(i_current), .sample_tick(sample_tick), .running(running)
    );

    // ---- izh_dac_bank (defaults: i_param=0, i_const=10.0; never programmed) ----
    wire [5:0]   cfg_addr;
    reg  [31:0]  cfg_data = 0;
    wire [3:0]   spike_flags;
    wire [127:0] i_mon;
    wire [31:0]  dbg;
    izh_dac_bank #(.ADDR_W(6)) u_bank (
        .clk(clk_50), .reset(neuron_rst), .prog_start(1'b0),
        .i_external(i_current),
        .cfg_addr(cfg_addr), .cfg_data(cfg_data),
        .spike_flags(spike_flags), .i_mon(i_mon), .debug_word(dbg)
    );

    // ---- capture = sample_tick | heartbeat (identical to top.v) ----
    reg [7:0] hb = 0;
    always @(posedge clk_50) if (neuron_rst) hb <= 8'd0; else hb <= hb + 8'd1;
    wire capture = sample_tick | (hb == 8'd0);

    wire [255:0] mon_words;
    cur_monitor_cdc #(.N(4), .SHIFT(8)) u_cdc (
        .src_clk(clk_50), .src_rst(neuron_rst), .i_mon(i_mon), .capture(capture),
        .dst_clk(gt_clk), .mon_words(mon_words)
    );

    wire [63:0] cur_source_word;
    cur_monitor_cdc #(.N(1), .SHIFT(8)) u_cur_source_cdc (
        .src_clk(clk_50), .src_rst(neuron_rst), .i_mon(i_current), .capture(capture),
        .dst_clk(gt_clk), .mon_words(cur_source_word)
    );

    function [15:0] scl(input [31:0] q);
        integer v;
        begin
            v = $signed(q) >>> 8;
            if (v > 32767)       scl = 16'h7FFF;
            else if (v < -32768) scl = 16'h8000;
            else                 scl = v[15:0];
        end
    endfunction

    integer i, errors;
    reg signed [31:0] amp;
    reg signed [15:0] sw, cw, monmin, monmax, curmin, curmax;
    reg signed [31:0] imn, imonmin, imonmax;

    initial begin
        errors = 0;
        // +/-80.0 (Q16.16) triangle over 50 samples, rest zero (like CURP 1 49 0x500000)
        amp = 32'sd5242880;
        for (i = 0; i < 1024; i = i + 1) begin
            if (i < 25)      cur_wave[i] = -amp + (2*amp*i) / 25;
            else if (i < 50) cur_wave[i] =  amp - (2*amp*(i-25)) / 25;
            else             cur_wave[i] = 32'sd0;
        end

        repeat (20) @(negedge clk_50);
        neuron_rst = 0;
        wait (u_cdc.fifo_wr_rst_busy === 1'b0 && u_cdc.fifo_rd_rst_busy === 1'b0 &&
              u_cur_source_cdc.fifo_wr_rst_busy === 1'b0 &&
              u_cur_source_cdc.fifo_rd_rst_busy === 1'b0);
        repeat (10) @(posedge gt_clk);
        @(negedge clk_50); run = 1; restart = 1;
        @(negedge clk_50); restart = 0;

        // sample i_mon (neuron domain) and mon_words (DAC domain) over many cycles
        monmin = 16'sh7FFF; monmax = 16'sh8000;
        curmin = 16'sh7FFF; curmax = 16'sh8000;
        imonmin = 32'sh7FFFFFFF; imonmax = 32'sh80000000;
        for (i = 0; i < 6000; i = i + 1) begin
            @(posedge gt_clk);
            sw = mon_words[15:0];               // monitor 0, sample 0
            cw = cur_source_word[15:0];         // pure current source, sample 0
            if (sw > monmax) monmax = sw;
            if (sw < monmin) monmin = sw;
            if (cw > curmax) curmax = cw;
            if (cw < curmin) curmin = cw;
            imn = i_mon[31:0];
            if (imn > imonmax) imonmax = imn;
            if (imn < imonmin) imonmin = imn;
        end

        $display("i_mon[ch0]   swing: min=%0d max=%0d range=%0d", imonmin, imonmax, imonmax - imonmin);
        $display("mon_words[ch0,s0] swing: min=%0d max=%0d range=%0d", monmin, monmax, monmax - monmin);
        $display("cur_source_word swing: min=%0d max=%0d range=%0d", curmin, curmax, curmax - curmin);
        $display("expected mon swing ~ %0d .. %0d", $signed(scl(32'sh80000000)), $signed(scl(32'h7FFFFFFF)));

        if ((imonmax - imonmin) < 1000) begin
            errors = errors + 1;
            $display("FAIL: i_mon is flat -> izh_dac_bank.i_mon does not track i_external");
        end
        if (($signed(monmax) - $signed(monmin)) < 1000) begin
            errors = errors + 1;
            $display("FAIL: mon_words is flat -> CDC/monitor chain does not propagate i_mon");
        end
        if (($signed(curmax) - $signed(curmin)) < 1000) begin
            errors = errors + 1;
            $display("FAIL: cur_source_word is flat -> pure current CDC does not propagate i_current");
        end
        if (!(curmin < monmin && curmax < monmax)) begin
            errors = errors + 1;
            $display("FAIL: pure current should not include +I_const monitor offset");
        end

        if (errors == 0)
            $display("TB_RESULT: PASS izh_monitor_integ (monitor and pure-current paths track injected current)");
        else
            $display("TB_RESULT: FAIL izh_monitor_integ (reproduces silence; bug is in this RTL chain)");
        $finish;
    end

    initial begin
        #800000;
        $display("TB_RESULT: FAIL izh_monitor_integ TIMEOUT");
        $finish;
    end
endmodule

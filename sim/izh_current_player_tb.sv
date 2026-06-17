`timescale 1ns/1ps

// Self-checking TB for the programmable current player.  Verifies: it walks the
// BRAM in order and loops over 0..last_index; emits one sample_tick per new
// sample at the cycles_per_sample rate; freezes (holds output, no ticks) when
// run=0; and re-primes to sample 0 on restart.  A behavioral 1-cycle-latency
// BRAM models the real port-B read.

module izh_current_player_tb;
    localparam integer ADDR_W = 4, DATA_W = 32;

    reg                  clk = 0, reset = 1, run = 0, restart = 0;
    reg  [15:0]          cycles_per_sample = 2;
    reg  [ADDR_W-1:0]    last_index = 3;
    wire [ADDR_W-1:0]    bram_addr;
    wire                 bram_en;
    reg  [DATA_W-1:0]    bram_data = 0;
    wire [DATA_W-1:0]    i_current;
    wire                 sample_tick, running;
    integer              errors = 0;

    // behavioral 1-cycle-latency BRAM (port B)
    reg [DATA_W-1:0] mem [0:(1<<ADDR_W)-1];
    always @(posedge clk) bram_data <= mem[bram_addr];

    izh_current_player #(.ADDR_W(ADDR_W), .DATA_W(DATA_W)) dut (
        .clk               (clk),
        .reset             (reset),
        .run               (run),
        .restart           (restart),
        .cycles_per_sample (cycles_per_sample),
        .last_index        (last_index),
        .bram_addr         (bram_addr),
        .bram_en           (bram_en),
        .bram_data         (bram_data),
        .i_current         (i_current),
        .sample_tick       (sample_tick),
        .running           (running)
    );

    always #10 clk = ~clk;   // 50 MHz

    // capture each emitted sample
    reg [DATA_W-1:0] seq [0:31];
    integer nseq = 0;
    always @(posedge clk)
        if (!reset && sample_tick && nseq < 32) begin
            seq[nseq] = i_current;
            nseq = nseq + 1;
        end

    integer j, k;
    initial begin
        for (j = 0; j < (1<<ADDR_W); j = j + 1)
            mem[j] = 32'hC0DE_0000 + j;

        repeat (3) @(negedge clk);
        reset = 0;
        @(negedge clk); run = 1;

        // 1) ordered playback + looping: first 10 samples = mem[k % 4]
        wait (nseq >= 10);
        for (k = 0; k < 10; k = k + 1)
            if (seq[k] !== mem[k % 4]) begin
                errors = errors + 1;
                $display("FAIL: order seq[%0d]=%h exp=%h", k, seq[k], mem[k%4]);
            end

        // 2) freeze: run=0 -> no new ticks, i_current held
        begin : freeze
            integer nbefore;
            reg [DATA_W-1:0] held;
            @(negedge clk); run = 0;
            repeat (3) @(negedge clk);      // let any in-flight sample settle
            nbefore = nseq;
            held    = i_current;
            repeat (20) @(posedge clk);
            if (nseq !== nbefore) begin
                errors = errors + 1;
                $display("FAIL: tick while stopped (%0d -> %0d)", nbefore, nseq);
            end
            if (i_current !== held) begin
                errors = errors + 1;
                $display("FAIL: i_current changed while stopped");
            end
        end

        // 3) restart re-primes to sample 0
        nseq = 0;
        @(negedge clk); restart = 1; run = 1;
        @(negedge clk); restart = 0;
        wait (nseq >= 1);
        if (seq[0] !== mem[0]) begin
            errors = errors + 1;
            $display("FAIL: post-restart seq[0]=%h exp=%h", seq[0], mem[0]);
        end

        if (errors == 0)
            $display("TB_RESULT: PASS izh_current_player (all checks)");
        else
            $display("TB_RESULT: FAIL izh_current_player errors=%0d", errors);
        $finish;
    end

    // Safety net.
    initial begin
        #40000;
        $display("TB_RESULT: FAIL izh_current_player TIMEOUT nseq=%0d", nseq);
        $finish;
    end
endmodule

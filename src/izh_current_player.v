`timescale 1ns/1ps

// Programmable current-source player -- runs in the neuron's clk_50 domain.
//
// Plays an arbitrary current waveform out of a dual-clock BRAM (the MicroBlaze
// loads it over AXI on port A; this reads it on port B) at a REGISTER-CONTROLLED
// rate, so a SMALL BRAM can synthesize a low-frequency signal: the read pointer
// advances once every `cycles_per_sample` clk cycles -- holding the sample in
// between -- and loops over samples 0..`last_index`. Adjusting cycles_per_sample
// scales the output frequency without needing a bigger BRAM.
//
// Playback is register-controlled for deterministic experiments:
//   run      = 1 plays, 0 holds (stop/pause without losing position)
//   restart  = 1-cycle pulse resets the read pointer to sample 0
//   reset    = global reset, clears everything
//
//   effective sample rate = f_clk / cycles_per_sample        (f_clk = 50 MHz)
//   loop period           = (last_index+1) * cycles_per_sample / f_clk
//
// The held output `i_current` (Q16.16) drives the neuron's I input and is the
// authoritative value to mirror to the monitor DAC via a downstream CDC.

module izh_current_player #(
    parameter integer ADDR_W = 12,      // 2**ADDR_W BRAM samples
    parameter integer DATA_W = 32       // Q16.16 current sample
) (
    input  wire               clk,              // clk_50 (neuron domain)
    input  wire               reset,            // global reset, active high

    // ---- register-controlled playback (already synchronized into clk) -------
    input  wire               run,              // 1 = play, 0 = hold/stop
    input  wire               restart,          // 1-cycle pulse: back to sample 0
    input  wire [15:0]        cycles_per_sample,// advance every N clk (0 -> 1)
    input  wire [ADDR_W-1:0]  last_index,       // last sample (0 -> full depth-1)

    // ---- waveform BRAM read port (port B, this clock domain) ----------------
    output reg  [ADDR_W-1:0]  bram_addr,        // sample index
    output wire               bram_en,
    input  wire [DATA_W-1:0]  bram_data,        // 1-cycle read latency

    // ---- outputs ------------------------------------------------------------
    output reg  [DATA_W-1:0]  i_current,        // held current sample (-> neuron + DAC)
    output reg                sample_tick,      // 1-cycle pulse when i_current updates
    output wire               running           // status: playback active
);
    localparam [ADDR_W-1:0] ZERO_ADDR = {ADDR_W{1'b0}};
    localparam [ADDR_W-1:0] FULL_LAST = {ADDR_W{1'b1}};      // depth-1

    wire [15:0]       eff_cps  = (cycles_per_sample == 16'd0) ? 16'd1 : cycles_per_sample;
    wire [ADDR_W-1:0] eff_last = (last_index == ZERO_ADDR)    ? FULL_LAST : last_index;

    reg [15:0] div_cnt = 16'd0;
    reg        active  = 1'b0;      // registered run (status)
    reg        primed  = 1'b0;      // BRAM read latency settled after (re)start

    assign bram_en = 1'b1;          // free-running read; the address selects data
    assign running = active;

    always @(posedge clk) begin
        sample_tick <= 1'b0;
        if (reset) begin
            bram_addr <= ZERO_ADDR;
            div_cnt   <= 16'd0;
            i_current <= {DATA_W{1'b0}};
            active    <= 1'b0;
            primed    <= 1'b0;
        end else if (restart) begin
            // deterministic experiment reset: pointer to 0, re-prime the read
            bram_addr <= ZERO_ADDR;
            div_cnt   <= 16'd0;
            primed    <= 1'b0;
            active    <= run;
        end else begin
            active <= run;
            if (run) begin
                if (!primed) begin
                    // one settle cycle so sample 0's BRAM data is valid first
                    primed  <= 1'b1;
                    div_cnt <= 16'd0;
                end else if (div_cnt >= eff_cps - 16'd1) begin
                    div_cnt     <= 16'd0;
                    i_current   <= bram_data;     // sample for the current address
                    sample_tick <= 1'b1;
                    bram_addr   <= (bram_addr == eff_last) ? ZERO_ADDR
                                                           : bram_addr + 1'b1;
                end else begin
                    div_cnt <= div_cnt + 16'd1;
                end
            end
            // run == 0: freeze (hold bram_addr, div_cnt, i_current) -> stop/pause
        end
    end

endmodule

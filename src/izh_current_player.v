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
//   hold_last= 1 plays 0..last_index once, then holds the last sample
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
    input  wire               hold_last,        // one-shot mode: hold at last_index
    input  wire [15:0]        cycles_per_sample,// advance every N clk (0 -> 1)
    input  wire [ADDR_W-1:0]  last_index,       // last sample (0 -> one sample)

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

    wire [15:0]       eff_cps  = (cycles_per_sample == 16'd0) ? 16'd1 : cycles_per_sample;
    wire [ADDR_W-1:0] eff_last = last_index;

    reg [15:0] div_cnt = 16'd0;
    reg [ADDR_W-1:0] out_idx = {ADDR_W{1'b0}};
    reg        active  = 1'b0;      // registered run (status)
    reg        primed  = 1'b0;      // BRAM data for out_idx is valid
    reg        done    = 1'b0;      // one-shot mode reached last_index

    wire [ADDR_W-1:0] out_idx_next =
        (out_idx == eff_last) ? ZERO_ADDR : out_idx + 1'b1;
    wire [ADDR_W-1:0] out_idx_next2 =
        (out_idx_next == eff_last) ? ZERO_ADDR : out_idx_next + 1'b1;

    assign bram_en = 1'b1;          // free-running read; the address selects data
    assign running = active && !done;

    always @(posedge clk) begin
        sample_tick <= 1'b0;
        if (reset) begin
            bram_addr <= ZERO_ADDR;
            div_cnt   <= 16'd0;
            out_idx   <= ZERO_ADDR;
            i_current <= {DATA_W{1'b0}};
            active    <= 1'b0;
            primed    <= 1'b0;
            done      <= 1'b0;
        end else if (restart) begin
            // deterministic experiment reset: pointer to 0, re-prime the read
            bram_addr <= ZERO_ADDR;
            div_cnt   <= 16'd0;
            out_idx   <= ZERO_ADDR;
            primed    <= 1'b0;
            active    <= run;
            done      <= 1'b0;
        end else begin
            active <= run;
            if (!hold_last)
                done <= 1'b0;
            if (run && !done) begin
                if (!primed) begin
                    // One settle cycle so sample 0's BRAM data is valid first.
                    // If cps=1, immediately prefetch sample 1 so the first
                    // two output ticks are sample 0, sample 1, not 0, 0.
                    primed  <= 1'b1;
                    div_cnt <= 16'd0;
                    bram_addr <= (eff_cps == 16'd1) ? out_idx_next : ZERO_ADDR;
                end else if (div_cnt >= eff_cps - 16'd1) begin
                    div_cnt     <= 16'd0;
                    i_current   <= bram_data;     // sample for out_idx
                    sample_tick <= 1'b1;
                    if (hold_last && (out_idx == eff_last)) begin
                        done      <= 1'b1;
                        bram_addr <= eff_last;
                    end else begin
                        out_idx <= out_idx_next;
                        if (eff_cps == 16'd1)
                            bram_addr <= out_idx_next2;
                    end
                end else begin
                    if ((eff_cps != 16'd1) && (div_cnt == eff_cps - 16'd2))
                        bram_addr <= out_idx_next; // prefetch one clk before tick
                    div_cnt <= div_cnt + 16'd1;
                end
            end
            // run == 0: freeze (hold bram_addr, div_cnt, i_current) -> stop/pause
        end
    end

endmodule

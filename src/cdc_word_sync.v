`timescale 1ns/1ps

// Word-coherent CDC for quasi-static multi-bit CONTROL words (register-file
// values written by firmware).  cdc_vector_sync is per-bit and can tear: bits
// of one source write land on different destination edges, so a consumer that
// samples the word continuously (e.g. the current player's cycles_per_sample,
// the DAC BRAM player's frame_count compare) can act on a value that never
// existed.  Here the synchronized word is only accepted into `dest` after two
// consecutive destination-clock samples agree.  A single source transition
// skews bits across at most one destination cycle through the 2-FF chains, so
// a torn combination can never satisfy the equality and `dest` steps
// atomically old -> new.  Requires the source word to be stable for a few
// destination cycles between writes -- true for AXI register writes.
module cdc_word_sync #(
    parameter integer WIDTH = 1
) (
    input  wire             dest_clk,
    input  wire             dest_rst,
    input  wire [WIDTH-1:0] src,
    output reg  [WIDTH-1:0] dest
);

    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [WIDTH-1:0] meta = {WIDTH{1'b0}};
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [WIDTH-1:0] sync = {WIDTH{1'b0}};
    reg [WIDTH-1:0] prev = {WIDTH{1'b0}};

    initial dest = {WIDTH{1'b0}};

    always @(posedge dest_clk) begin
        meta <= src;
        sync <= meta;
        prev <= sync;
        if (sync == prev)
            dest <= sync;
    end

    wire unused_dest_rst = dest_rst;

endmodule

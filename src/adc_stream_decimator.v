`timescale 1ns/1ps

// Continuous decimating streamer for the PS DDR DMA path.
//
// Input: one 128-bit frame per clk when data_valid, carrying four
// chronological 16-bit samples for each of two channels:
//   frame_data[63:0]   = ch_even samples s0..s3 (s0 in [15:0])
//   frame_data[127:64] = ch_odd  samples s0..s3
//
// Uniform decimation keep-1-of-D per channel with D a multiple of 4
// (minimum 4): lane 0 of every (D/4)-th input beat is kept, so kept
// samples are exactly D input samples apart. Four kept samples per
// channel are packed into one output beat with the SAME frame layout
// as the input, so host-side decoding is unchanged (sample period
// becomes D ns at 1 GS/s).
//
// tlast is asserted every CHUNK_BEATS output beats so each chunk
// closes exactly one cyclic scatter-gather descriptor downstream.
//
// This block is intentionally a drop-in AXIS producer: replacing the
// keep-1-of-D core with a CIC + halfband FIR later only changes the
// internals, not the interface.

module adc_stream_decimator #(
    parameter integer CHUNK_BEATS = 8192,  // 8192 x 16 B = 128 KB chunks
    parameter integer FIFO_DEPTH  = 256    // power of two
) (
    input  wire         clk,
    input  wire         rst,

    input  wire         enable,        // RW6[31], synchronized to clk
    input  wire [15:0]  decim,         // RW6[15:0] = D, synchronized to clk

    input  wire         data_valid,
    input  wire [127:0] frame_data,

    output reg  [127:0] m_axis_tdata,
    output wire [15:0]  m_axis_tkeep,
    output reg          m_axis_tlast,
    output reg          m_axis_tvalid,
    input  wire         m_axis_tready,

    output wire [31:0]  status
);

    localparam integer FIFO_AW = $clog2(FIFO_DEPTH);
    localparam integer CHUNK_W = $clog2(CHUNK_BEATS);

    assign m_axis_tkeep = 16'hFFFF;

    // beats between kept samples = max(D/4, 1)
    wire [13:0] beats_per_keep = (decim[15:2] == 14'd0) ? 14'd1 : decim[15:2];

    reg  [13:0] beat_cnt = 14'd0;
    reg  [1:0]  acc_cnt = 2'd0;
    reg  [47:0] acc_even = 48'd0;       // three pending kept samples
    reg  [47:0] acc_odd = 48'd0;
    reg  [CHUNK_W-1:0] chunk_cnt = {CHUNK_W{1'b0}};

    wire keep_beat = enable & data_valid & (beat_cnt == 14'd0);
    wire [15:0] kept_even = frame_data[15:0];
    wire [15:0] kept_odd = frame_data[79:64];
    wire emit_beat = keep_beat & (acc_cnt == 2'd3);

    // ---- small sync FIFO (distributed RAM) -------------------------------
    reg [128:0] fifo_mem [0:FIFO_DEPTH-1];
    reg [FIFO_AW:0] wr_ptr = {FIFO_AW+1{1'b0}};
    reg [FIFO_AW:0] rd_ptr = {FIFO_AW+1{1'b0}};
    wire fifo_empty = (wr_ptr == rd_ptr);
    wire fifo_full = (wr_ptr - rd_ptr) == FIFO_DEPTH[FIFO_AW:0];

    reg        overflow_sticky = 1'b0;
    reg [15:0] overflow_count = 16'd0;

    wire fifo_push = emit_beat & ~fifo_full;
    wire out_advance = ~m_axis_tvalid | (m_axis_tvalid & m_axis_tready);
    wire fifo_pop = ~fifo_empty & out_advance;

    always @(posedge clk) begin
        if (rst | ~enable) begin
            beat_cnt <= 14'd0;
            acc_cnt <= 2'd0;
            acc_even <= 48'd0;
            acc_odd <= 48'd0;
            chunk_cnt <= {CHUNK_W{1'b0}};
            wr_ptr <= {FIFO_AW+1{1'b0}};
            rd_ptr <= {FIFO_AW+1{1'b0}};
            m_axis_tvalid <= 1'b0;
            m_axis_tlast <= 1'b0;
            m_axis_tdata <= 128'd0;
            if (rst) begin
                overflow_sticky <= 1'b0;
                overflow_count <= 16'd0;
            end
        end else begin
            if (data_valid) begin
                beat_cnt <= (beat_cnt == beats_per_keep - 14'd1) ? 14'd0
                                                                 : beat_cnt + 14'd1;
            end

            if (keep_beat) begin
                if (acc_cnt == 2'd3) begin
                    if (~fifo_full) begin
                        fifo_mem[wr_ptr[FIFO_AW-1:0]] <= {
                            (chunk_cnt == CHUNK_BEATS[CHUNK_W-1:0] - 1'b1),
                            kept_odd, acc_odd,
                            kept_even, acc_even
                        };
                        wr_ptr <= wr_ptr + 1'b1;
                        chunk_cnt <= chunk_cnt + 1'b1;
                    end else begin
                        overflow_sticky <= 1'b1;
                        overflow_count <= overflow_count + 16'd1;
                    end
                    acc_cnt <= 2'd0;
                end else begin
                    acc_even <= {kept_even, acc_even[47:16]};
                    acc_odd <= {kept_odd, acc_odd[47:16]};
                    acc_cnt <= acc_cnt + 2'd1;
                end
            end

            if (fifo_pop) begin
                {m_axis_tlast, m_axis_tdata} <= fifo_mem[rd_ptr[FIFO_AW-1:0]];
                m_axis_tvalid <= 1'b1;
                rd_ptr <= rd_ptr + 1'b1;
            end else if (out_advance) begin
                m_axis_tvalid <= 1'b0;
                m_axis_tlast <= 1'b0;
            end
        end
    end

    assign status = {
        8'hDC,
        overflow_sticky,
        enable,
        m_axis_tvalid,
        m_axis_tready,
        overflow_count[11:0],
        4'd0,
        beats_per_keep[3:0]
    };

endmodule

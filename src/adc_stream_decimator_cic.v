`timescale 1ns/1ps

// Anti-aliasing decimating streamer (CIC) for the PS DDR DMA path.
//
// Drop-in alternative to adc_stream_decimator: identical AXIS interface and
// identical output frame layout, but instead of keep-1-of-D (which aliases
// everything above the decimated Nyquist straight into the passband) this
// path low-pass filters before downsampling, so the stream carries the true
// in-band waveform.
//
// Input: one 128-bit frame per clk when data_valid, four chronological 16-bit
// samples for each of two channels (1 GS/s/ch, so the beat rate is 250 MHz):
//   frame_data[63:0]   = ch_even samples s0..s3 (s0 in [15:0])
//   frame_data[127:64] = ch_odd  samples s0..s3
//
// Decimation chain per channel (FIXED total D = 128):
//   1. boxcar-4  : sum the four samples in each beat  -> 1 sample/beat,  /4
//                  (a first-order CIC / moving average, gain 4 = 2^2)
//   2. cic3      : 3-stage CIC, R=32                  -> 1 sample/32 beats /32
//                  (gain (R*M)^N = 32^3 = 2^15)
//   Total gain 2^2 * 2^15 = 2^17 -> OUT_SHIFT=17 normalizes back to 16-bit.
//   Total decimation 4*32 = 128 -> 7.8125 MS/s/ch, Nyquist 3.906 MHz.
//
// The `decim` input is accepted for port compatibility but ignored: this core
// runs at the fixed D=128 the chain is sized for. Stream the rest of the
// design at STRM 128 so both chips share one timebase for an A/B compare.
//
// Four decimated samples per channel are packed into one output beat with the
// SAME layout as the input frame, so host-side decoding is unchanged. tlast is
// asserted every CHUNK_BEATS output beats to close one cyclic SG descriptor.

module adc_stream_decimator_cic #(
    parameter integer CHUNK_BEATS = 8192,  // 8192 x 16 B = 128 KB chunks
    parameter integer FIFO_DEPTH  = 256    // power of two
) (
    input  wire         clk,
    input  wire         rst,

    input  wire         enable,        // RW6[31], synchronized to clk
    input  wire [15:0]  decim,         // accepted but ignored (fixed D=128)

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

    wire core_rst = rst | ~enable;
    wire in_valid = enable & data_valid;

    // ---- stage 1: boxcar-4 over the four samples in each beat -------------
    // Each sample is signed 16-bit; the sum of four is signed 18-bit.
    function [17:0] sext16_18;
        input [15:0] v;
        sext16_18 = {{2{v[15]}}, v};
    endfunction

    wire signed [17:0] box_even =
        $signed(sext16_18(frame_data[15:0]))   + $signed(sext16_18(frame_data[31:16])) +
        $signed(sext16_18(frame_data[47:32]))  + $signed(sext16_18(frame_data[63:48]));
    wire signed [17:0] box_odd =
        $signed(sext16_18(frame_data[79:64]))  + $signed(sext16_18(frame_data[95:80])) +
        $signed(sext16_18(frame_data[111:96])) + $signed(sext16_18(frame_data[127:112]));

    // ---- stage 2: 3-stage CIC, R=32, one per channel ---------------------
    wire        even_valid, odd_valid;
    wire signed [15:0] even_out, odd_out;

    cic3_decimate #(
        .DECIM(32), .IN_W(18), .ACC_W(36), .OUT_SHIFT(17)
    ) u_cic_even (
        .clk(clk), .rst(core_rst),
        .in_valid(in_valid), .in_data(box_even),
        .out_valid(even_valid), .out_data(even_out)
    );

    cic3_decimate #(
        .DECIM(32), .IN_W(18), .ACC_W(36), .OUT_SHIFT(17)
    ) u_cic_odd (
        .clk(clk), .rst(core_rst),
        .in_valid(in_valid), .in_data(box_odd),
        .out_valid(odd_valid), .out_data(odd_out)
    );

    // even and odd CICs are fed identically, so they emit in lockstep.
    wire decim_valid = even_valid;

    // ---- pack four decimated samples per channel into one beat ------------
    reg  [1:0]  acc_cnt = 2'd0;
    reg  [47:0] acc_even = 48'd0;       // three pending samples
    reg  [47:0] acc_odd = 48'd0;
    reg  [CHUNK_W-1:0] chunk_cnt = {CHUNK_W{1'b0}};

    // ---- small sync FIFO (distributed RAM) -------------------------------
    reg [128:0] fifo_mem [0:FIFO_DEPTH-1];
    reg [FIFO_AW:0] wr_ptr = {FIFO_AW+1{1'b0}};
    reg [FIFO_AW:0] rd_ptr = {FIFO_AW+1{1'b0}};
    wire fifo_empty = (wr_ptr == rd_ptr);
    wire fifo_full = (wr_ptr - rd_ptr) == FIFO_DEPTH[FIFO_AW:0];

    reg        overflow_sticky = 1'b0;
    reg [15:0] overflow_count = 16'd0;

    wire out_advance = ~m_axis_tvalid | (m_axis_tvalid & m_axis_tready);
    wire fifo_pop = ~fifo_empty & out_advance;

    always @(posedge clk) begin
        if (rst | ~enable) begin
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
            if (decim_valid) begin
                if (acc_cnt == 2'd3) begin
                    if (~fifo_full) begin
                        fifo_mem[wr_ptr[FIFO_AW-1:0]] <= {
                            (chunk_cnt == CHUNK_BEATS[CHUNK_W-1:0] - 1'b1),
                            odd_out, acc_odd,
                            even_out, acc_even
                        };
                        wr_ptr <= wr_ptr + 1'b1;
                        chunk_cnt <= chunk_cnt + 1'b1;
                    end else begin
                        overflow_sticky <= 1'b1;
                        overflow_count <= overflow_count + 16'd1;
                    end
                    acc_cnt <= 2'd0;
                end else begin
                    acc_even <= {even_out, acc_even[47:16]};
                    acc_odd <= {odd_out, acc_odd[47:16]};
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
        8'hC1,                 // tag: CIC path (vs 8'hDC keep-1-of-D)
        overflow_sticky,
        enable,
        m_axis_tvalid,
        m_axis_tready,
        overflow_count[11:0],
        8'd128                 // effective fixed decimation
    };

endmodule

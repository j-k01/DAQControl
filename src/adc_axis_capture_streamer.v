`timescale 1ns/1ps

module adc_axis_capture_streamer #(
    parameter integer CAPTURE_FRAMES = 4096
) (
    input  wire         clk,
    input  wire         rst,
    input  wire         start,
    input  wire         data_valid,
    input  wire [127:0] frame_data,

    output reg  [127:0] m_axis_tdata,
    output wire [15:0]  m_axis_tkeep,
    output reg          m_axis_tlast,
    output reg          m_axis_tvalid,
    input  wire         m_axis_tready,

    output wire [31:0]  status
);

    localparam integer COUNT_W = $clog2(CAPTURE_FRAMES + 1);

    reg                 running = 1'b0;
    reg                 done = 1'b0;
    reg [COUNT_W-1:0]   accepted_count = {COUNT_W{1'b0}};
    reg [15:0]          stalled_count = 16'd0;

    wire output_fire = m_axis_tvalid & m_axis_tready;
    wire load_frame = running & data_valid & (~m_axis_tvalid | output_fire);
    wire next_is_last = accepted_count == (CAPTURE_FRAMES - 1);

    assign m_axis_tkeep = 16'hFFFF;

    always @(posedge clk) begin
        if (rst) begin
            running <= 1'b0;
            done <= 1'b0;
            accepted_count <= {COUNT_W{1'b0}};
            stalled_count <= 16'd0;
            m_axis_tdata <= 128'd0;
            m_axis_tlast <= 1'b0;
            m_axis_tvalid <= 1'b0;
        end else if (start) begin
            running <= 1'b1;
            done <= 1'b0;
            accepted_count <= {COUNT_W{1'b0}};
            stalled_count <= 16'd0;
            m_axis_tdata <= 128'd0;
            m_axis_tlast <= 1'b0;
            m_axis_tvalid <= 1'b0;
        end else begin
            if (output_fire) begin
                if (m_axis_tlast) begin
                    running <= 1'b0;
                    done <= 1'b1;
                end
                m_axis_tvalid <= 1'b0;
                m_axis_tlast <= 1'b0;
            end

            if (load_frame) begin
                m_axis_tdata <= frame_data;
                m_axis_tlast <= next_is_last;
                m_axis_tvalid <= 1'b1;
                if (!next_is_last) begin
                    accepted_count <= accepted_count + 1'b1;
                end else begin
                    accepted_count <= CAPTURE_FRAMES;
                end
            end else if (running & data_valid & m_axis_tvalid & ~m_axis_tready) begin
                stalled_count <= stalled_count + 1'b1;
            end
        end
    end

    assign status = {
        8'hD1,
        done,
        running,
        m_axis_tvalid,
        m_axis_tready,
        stalled_count[11:0],
        accepted_count[7:0]
    };

endmodule

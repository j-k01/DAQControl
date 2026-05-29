`timescale 1ns/1ps

module signal_activity_monitor #(
    parameter integer COUNTER_WIDTH = 32,
    parameter integer REF_WINDOW_CYCLES = 200_000_000
) (
    input  wire                     ref_clk,
    input  wire                     ref_rst,
    input  wire                     test_signal,
    output reg  [COUNTER_WIDTH-1:0] last_count,
    output reg                      seen
);

    (* ASYNC_REG = "TRUE" *) reg signal_meta = 1'b0;
    (* ASYNC_REG = "TRUE" *) reg signal_sync = 1'b0;
    reg signal_prev = 1'b0;

    reg [COUNTER_WIDTH-1:0] edge_count = {COUNTER_WIDTH{1'b0}};
    reg [31:0]              window_count = 32'd0;

    wire signal_edge = signal_sync ^ signal_prev;
    wire [COUNTER_WIDTH-1:0] edge_inc = {{(COUNTER_WIDTH-1){1'b0}}, signal_edge};
    wire [COUNTER_WIDTH-1:0] next_edge_count = edge_count + edge_inc;

    always @(posedge ref_clk) begin
        signal_meta <= test_signal;
        signal_sync <= signal_meta;
        signal_prev <= signal_sync;

        if (ref_rst) begin
            last_count   <= {COUNTER_WIDTH{1'b0}};
            seen         <= 1'b0;
            edge_count   <= {COUNTER_WIDTH{1'b0}};
            window_count <= 32'd0;
        end else if (window_count == REF_WINDOW_CYCLES - 1) begin
            last_count   <= next_edge_count;
            seen         <= |next_edge_count;
            edge_count   <= {COUNTER_WIDTH{1'b0}};
            window_count <= 32'd0;
        end else begin
            edge_count   <= next_edge_count;
            window_count <= window_count + 1'b1;
        end
    end

endmodule

`timescale 1ns/1ps

module cdc_vector_sync #(
    parameter integer WIDTH = 1
) (
    input  wire             dest_clk,
    input  wire             dest_rst,
    input  wire [WIDTH-1:0] src,
    output wire [WIDTH-1:0] dest
);

    (* ASYNC_REG = "TRUE" *) reg [WIDTH-1:0] meta = {WIDTH{1'b0}};
    (* ASYNC_REG = "TRUE" *) reg [WIDTH-1:0] sync = {WIDTH{1'b0}};

    always @(posedge dest_clk) begin
        if (dest_rst) begin
            meta <= {WIDTH{1'b0}};
            sync <= {WIDTH{1'b0}};
        end else begin
            meta <= src;
            sync <= meta;
        end
    end

    assign dest = sync;

endmodule

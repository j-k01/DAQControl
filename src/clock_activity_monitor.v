module clock_activity_monitor #(
    parameter integer COUNTER_WIDTH = 32
) (
    input  wire                     ref_clk,
    input  wire                     ref_rst,
    input  wire                     test_clk,
    output reg  [COUNTER_WIDTH-1:0] last_count,
    output reg                      seen
);

    reg [COUNTER_WIDTH-1:0] test_count = {COUNTER_WIDTH{1'b0}};
    always @(posedge test_clk) begin
        test_count <= test_count + 1'b1;
    end

    wire [COUNTER_WIDTH-1:0] test_gray = test_count ^ (test_count >> 1);

    (* ASYNC_REG = "TRUE" *) reg [COUNTER_WIDTH-1:0] gray_meta = {COUNTER_WIDTH{1'b0}};
    (* ASYNC_REG = "TRUE" *) reg [COUNTER_WIDTH-1:0] gray_sync = {COUNTER_WIDTH{1'b0}};

    integer i;
    reg [COUNTER_WIDTH-1:0] sync_bin;
    reg [COUNTER_WIDTH-1:0] prev_bin;
    reg [27:0]              window_count;

    always @* begin
        sync_bin[COUNTER_WIDTH-1] = gray_sync[COUNTER_WIDTH-1];
        for (i = COUNTER_WIDTH-2; i >= 0; i = i - 1) begin
            sync_bin[i] = sync_bin[i+1] ^ gray_sync[i];
        end
    end

    always @(posedge ref_clk) begin
        gray_meta <= test_gray;
        gray_sync <= gray_meta;

        if (ref_rst) begin
            last_count   <= {COUNTER_WIDTH{1'b0}};
            seen         <= 1'b0;
            prev_bin     <= {COUNTER_WIDTH{1'b0}};
            window_count <= 28'd0;
        end else if (window_count == 28'd199_999_999) begin
            last_count   <= sync_bin - prev_bin;
            seen         <= |(sync_bin - prev_bin);
            prev_bin     <= sync_bin;
            window_count <= 28'd0;
        end else begin
            window_count <= window_count + 1'b1;
        end
    end

endmodule

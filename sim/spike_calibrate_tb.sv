`timescale 1ns/1ps

// Self-checking testbench for spike_calibrate: drives random beats through
// random (gain, offset) settings and compares against a golden model of
// y = sat16((sample * gain) >>> 15 + offset), including the gain==0 -> unity
// default and both saturation rails.

module spike_calibrate_tb;
    reg         clk = 1'b0;
    reg         rst = 1'b1;
    reg  [31:0] cal = 32'd0;
    reg  [63:0] in_word = 64'd0;
    wire [63:0] out_word;

    spike_calibrate dut (
        .clk      (clk),
        .rst      (rst),
        .cal      (cal),
        .in_word  (in_word),
        .out_word (out_word)
    );

    always #2 clk = ~clk;   // 250 MHz

    // 2-deep shadow pipeline of (input, cal) matching the DUT latency
    reg [63:0] in_d1, in_d2;
    reg [31:0] cal_d1, cal_d2;
    always @(posedge clk) begin
        in_d1  <= in_word;  in_d2  <= in_d1;
        cal_d1 <= cal;      cal_d2 <= cal_d1;
    end

    function [15:0] golden;
        input [15:0] sample;
        input [31:0] c;
        reg [15:0] g;
        integer sum;
        begin
            g = (c[15:0] == 16'd0) ? 16'h8000 : c[15:0];
            sum = ($signed(sample) * $signed({1'b0, g})) >>> 15;
            sum = sum + $signed(c[31:16]);
            if (sum > 32767)       golden = 16'h7FFF;
            else if (sum < -32768) golden = 16'h8000;
            else                   golden = sum[15:0];
        end
    endfunction

    integer n, lane, errors = 0;
    reg [15:0] exp, got;
    reg [31:0] rnd;

    initial begin
        repeat (4) @(posedge clk);
        rst = 1'b0;
        @(posedge clk);

        for (n = 0; n < 20000; n = n + 1) begin
            // random data; bias some cases to the corners
            in_word = {$random, $random};
            case (n % 7)
                0: cal = 32'd0;                              // unity, no offset
                1: cal = {16'd0, 16'h8000};                  // explicit unity
                2: cal = {16'h7FFF, 16'hFFFF};               // max gain + max offset
                3: cal = {16'h8000, 16'hFFFF};               // max gain + min offset
                4: begin rnd = $random; cal = {rnd[15:0], 16'h0001}; end // near-zero gain
                default: cal = $random;
            endcase
            @(posedge clk);
            #1;
            if (n >= 3) begin
                for (lane = 0; lane < 4; lane = lane + 1) begin
                    exp = golden(in_d2[lane*16 +: 16], cal_d2);
                    got = out_word[lane*16 +: 16];
                    if (exp !== got) begin
                        errors = errors + 1;
                        if (errors < 10)
                            $display("MISMATCH n=%0d lane=%0d in=%h cal=%h exp=%h got=%h",
                                     n, lane, in_d2[lane*16 +: 16], cal_d2, exp, got);
                    end
                end
            end
        end

        if (errors == 0) $display("PASS: spike_calibrate_tb 20000 vectors clean");
        else             $display("FAIL: %0d mismatches", errors);
        $finish;
    end
endmodule

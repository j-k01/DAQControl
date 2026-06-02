`timescale 1ns/1ps

module dataplane_bram_tb;
    reg clk = 1'b0;
    always #5 clk = ~clk;

    task tick;
        begin
            @(posedge clk);
            #1;
        end
    endtask

    task check32;
        input [255:0] label;
        input [31:0] actual;
        input [31:0] expected;
        begin
            if (actual !== expected) begin
                $display("FAIL %0s actual=0x%08x expected=0x%08x", label, actual, expected);
                $fatal;
            end
        end
    endtask

    task check64;
        input [255:0] label;
        input [63:0] actual;
        input [63:0] expected;
        begin
            if (actual !== expected) begin
                $display("FAIL %0s actual=0x%016x expected=0x%016x", label, actual, expected);
                $fatal;
            end
        end
    endtask

    task check128;
        input [255:0] label;
        input [127:0] actual;
        input [127:0] expected;
        begin
            if (actual !== expected) begin
                $display("FAIL %0s actual=0x%032x expected=0x%032x", label, actual, expected);
                $fatal;
            end
        end
    endtask

    reg         dac_rst = 1'b1;
    reg         dac_enable = 1'b0;
    reg         dac_restart = 1'b0;
    reg  [23:0] dac_frame_count = 24'd4;
    wire [31:0] dac_bram_addr;
    wire        dac_bram_clk;
    wire [63:0] dac_bram_din;
    reg  [63:0] dac_bram_dout = 64'd0;
    wire        dac_bram_en;
    wire        dac_bram_rst;
    wire [7:0]  dac_bram_we;
    wire [63:0] dac_program_word;
    wire [31:0] dac_status;

    reg [63:0] dac_mem [0:7];

    dac_bram_player #(
        .BRAM_DEPTH_WORDS (16)
    ) u_dac_bram_player (
        .clk          (clk),
        .rst          (dac_rst),
        .enable       (dac_enable),
        .restart      (dac_restart),
        .frame_count  (dac_frame_count),
        .bram_addr    (dac_bram_addr),
        .bram_clk     (dac_bram_clk),
        .bram_din     (dac_bram_din),
        .bram_dout    (dac_bram_dout),
        .bram_en      (dac_bram_en),
        .bram_rst     (dac_bram_rst),
        .bram_we      (dac_bram_we),
        .program_word (dac_program_word),
        .status       (dac_status)
    );

    always @(posedge dac_bram_clk) begin
        if (dac_bram_en) begin
            dac_bram_dout <= dac_mem[dac_bram_addr[31:3]];
        end
    end

    reg         adc_rst = 1'b1;
    reg         adc_start = 1'b0;
    reg         adc_valid = 1'b0;
    reg  [31:0] adc_a_low = 32'd0;
    reg  [31:0] adc_a_high = 32'd0;
    reg  [31:0] adc_b_low = 32'd0;
    reg  [31:0] adc_b_high = 32'd0;
    wire [31:0] adc_bram_addr;
    wire        adc_bram_clk;
    wire [127:0] adc_bram_din;
    wire [127:0] adc_bram_dout;
    wire        adc_bram_en;
    wire        adc_bram_rst;
    wire [15:0] adc_bram_we;
    wire [31:0] adc_status;

    reg [127:0] adc_mem [0:3];
    assign adc_bram_dout = 128'd0;

    adc_bram_capture #(
        .CAPTURE_FRAMES (4)
    ) u_adc_bram_capture (
        .clk           (clk),
        .rst           (adc_rst),
        .start         (adc_start),
        .data_valid    (adc_valid),
        .sample_a_low  (adc_a_low),
        .sample_a_high (adc_a_high),
        .sample_b_low  (adc_b_low),
        .sample_b_high (adc_b_high),
        .bram_addr     (adc_bram_addr),
        .bram_clk      (adc_bram_clk),
        .bram_din      (adc_bram_din),
        .bram_dout     (adc_bram_dout),
        .bram_en       (adc_bram_en),
        .bram_rst      (adc_bram_rst),
        .bram_we       (adc_bram_we),
        .status        (adc_status)
    );

    always @(posedge adc_bram_clk) begin
        if (adc_bram_en && |adc_bram_we) begin
            adc_mem[adc_bram_addr[31:4]] <= adc_bram_din;
        end
    end

    integer i;
    initial begin
        for (i = 0; i < 8; i = i + 1) begin
            dac_mem[i] = 64'h1000_0000_0000_0000 + i;
        end
        for (i = 0; i < 4; i = i + 1) begin
            adc_mem[i] = 128'd0;
        end

        tick();
        dac_rst = 1'b0;
        dac_enable = 1'b1;

        tick();
        check64("dac first latency bubble", dac_program_word, 64'd0);
        tick();
        check64("dac frame 0", dac_program_word, dac_mem[0]);
        tick();
        check64("dac frame 1", dac_program_word, dac_mem[1]);
        tick();
        check64("dac frame 2", dac_program_word, dac_mem[2]);
        tick();
        check64("dac frame 3", dac_program_word, dac_mem[3]);
        tick();
        check64("dac loops at frame_count", dac_program_word, dac_mem[0]);
        check32("dac status marker", {dac_status[31:24], 24'd0}, 32'hD4000000);
        if (!dac_status[22]) begin
            $display("FAIL dac valid status bit not set: 0x%08x", dac_status);
            $fatal;
        end

        dac_restart = 1'b1;
        tick();
        dac_restart = 1'b0;
        tick();
        check64("dac restart latency bubble", dac_program_word, dac_mem[0]);
        tick();
        check64("dac restarts at frame 0", dac_program_word, dac_mem[0]);

        dac_enable = 1'b0;
        tick();
        check64("dac disable clears output", dac_program_word, 64'd0);

        dac_frame_count = 24'd1;
        dac_enable = 1'b1;
        tick();
        check64("dac one-frame first bubble", dac_program_word, 64'd0);
        tick();
        check64("dac one-frame emits frame 0", dac_program_word, dac_mem[0]);
        tick();
        check64("dac one-frame loops frame 0", dac_program_word, dac_mem[0]);
        tick();
        check64("dac one-frame keeps looping frame 0", dac_program_word, dac_mem[0]);

        dac_enable = 1'b0;
        tick();
        check64("dac second disable clears output", dac_program_word, 64'd0);

        dac_frame_count = 24'd0;
        dac_enable = 1'b1;
        tick();
        check64("dac zero-count full-depth bubble", dac_program_word, 64'd0);
        for (i = 0; i < 8; i = i + 1) begin
            tick();
            check64("dac zero-count full-depth frame", dac_program_word, dac_mem[i]);
        end
        tick();
        check64("dac zero-count loops full depth", dac_program_word, dac_mem[0]);

        dac_enable = 1'b0;
        tick();

        adc_rst = 1'b0;
        adc_valid = 1'b1;
        adc_start = 1'b1;
        tick();
        adc_start = 1'b0;

        for (i = 0; i < 4; i = i + 1) begin
            adc_a_low  = 32'hA000_0000 + i;
            adc_a_high = 32'hA100_0000 + i;
            adc_b_low  = 32'hB000_0000 + i;
            adc_b_high = 32'hB100_0000 + i;
            tick();
        end

        check128("adc frame 0 packing", adc_mem[0], {
            32'hB100_0000, 32'hB000_0000, 32'hA100_0000, 32'hA000_0000
        });
        check128("adc frame 3 packing", adc_mem[3], {
            32'hB100_0003, 32'hB000_0003, 32'hA100_0003, 32'hA000_0003
        });
        check32("adc status marker", {adc_status[31:24], 24'd0}, 32'hC4000000);
        if (!adc_status[19] || adc_status[18] || adc_status[15:0] != 16'd4) begin
            $display("FAIL adc status expected done/count=4, got 0x%08x", adc_status);
            $fatal;
        end

        if (|dac_bram_din || |dac_bram_we || dac_bram_rst) begin
            $display("FAIL dac player should never write BRAM");
            $fatal;
        end
        if (adc_bram_rst) begin
            $display("FAIL adc capture should not assert BRAM reset");
            $fatal;
        end

        $display("dataplane_bram_tb passed");
        $finish;
    end
endmodule

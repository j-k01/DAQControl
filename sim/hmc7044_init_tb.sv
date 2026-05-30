`timescale 1ns/1ps

module hmc7044_init_tb;
    reg clk = 1'b0;
    reg rst = 1'b1;

    wire busy;
    wire done;
    wire reset_out;
    wire spi_cs_n;
    wire spi_sclk;
    wire spi_sdio_o;
    wire spi_sdio_oe;
    wire [7:0] step_index;
    wire [11:0] last_addr;
    wire [7:0] last_data;
    wire readback_done;
    wire readback_sdio_stuck;
    wire [3:0] readback_index;
    wire [11:0] readback_last_addr;
    wire [7:0] readback_last_data;
    wire [31:0] readback_id_word;
    wire [31:0] readback_alarm_word;
    wire [31:0] readback_pll1_word;
    wire [31:0] readback_pll2_word;

    hmc7044_init #(
        .CLK_HZ           (1),
        .SPI_HZ           (1_000_000),
        .RESET_ASSERT_US  (0),
        .RESET_RELEASE_US (0)
    ) dut (
        .clk         (clk),
        .rst         (rst),
        .spi_sdio_i  (1'b1),
        .busy        (busy),
        .done        (done),
        .reset_out   (reset_out),
        .spi_cs_n    (spi_cs_n),
        .spi_sclk    (spi_sclk),
        .spi_sdio_o  (spi_sdio_o),
        .spi_sdio_oe (spi_sdio_oe),
        .step_index  (step_index),
        .last_addr   (last_addr),
        .last_data   (last_data),
        .readback_done       (readback_done),
        .readback_sdio_stuck (readback_sdio_stuck),
        .readback_index      (readback_index),
        .readback_last_addr  (readback_last_addr),
        .readback_last_data  (readback_last_data),
        .readback_id_word    (readback_id_word),
        .readback_alarm_word (readback_alarm_word),
        .readback_pll1_word  (readback_pll1_word),
        .readback_pll2_word  (readback_pll2_word)
    );

    always #5 clk = ~clk;

    integer errors = 0;
    integer bit_count = 0;
    reg [23:0] first_word = 24'd0;
    time sdio_change_time = 0;

    always @(spi_sdio_o) begin
        sdio_change_time = $time;
    end

    always @(posedge spi_sclk) begin
        time rise_time;
        rise_time = $time;
        #1;
        if (!spi_cs_n && spi_sdio_oe && sdio_change_time == rise_time) begin
            $display("FAIL: SDIO changed on SCLK rising edge at %0t", rise_time);
            errors = errors + 1;
        end
        if (!spi_cs_n && bit_count < 24) begin
            first_word = {first_word[22:0], spi_sdio_o};
            bit_count = bit_count + 1;
        end
    end

    initial begin
        #25 rst = 1'b0;

        wait (bit_count == 24);
        #10;
        if (first_word !== 24'h000001) begin
            $display("FAIL: first HMC write expected 0x000001 got 0x%06x", first_word);
            errors = errors + 1;
        end else begin
            $display("PASS: first HMC write = 0x%06x", first_word);
        end

        wait (done);
        #10;
        if (!readback_done) begin
            $display("FAIL: HMC readback did not complete");
            errors = errors + 1;
        end
        if (!readback_sdio_stuck) begin
            $display("FAIL: stuck-high SDIO readback was not detected");
            errors = errors + 1;
        end
        if (readback_id_word[23:0] !== 24'hffffff) begin
            $display("FAIL: tied-high readback expected 0xffffff got 0x%06x", readback_id_word[23:0]);
            errors = errors + 1;
        end
        if (readback_index !== 4'd13) begin
            $display("FAIL: readback index expected 13 got %0d", readback_index);
            errors = errors + 1;
        end

        if (errors == 0) begin
            $display("HMC7044 SPI TEST PASSED");
        end else begin
            $display("HMC7044 SPI TEST FAILED with %0d errors", errors);
            $fatal(1);
        end
        $finish;
    end
endmodule

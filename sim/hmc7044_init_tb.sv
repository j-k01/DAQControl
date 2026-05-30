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

    hmc7044_init #(
        .CLK_HZ           (100_000_000),
        .SPI_HZ           (10_000_000),
        .RESET_ASSERT_US  (0),
        .RESET_RELEASE_US (0)
    ) dut (
        .clk         (clk),
        .rst         (rst),
        .busy        (busy),
        .done        (done),
        .reset_out   (reset_out),
        .spi_cs_n    (spi_cs_n),
        .spi_sclk    (spi_sclk),
        .spi_sdio_o  (spi_sdio_o),
        .spi_sdio_oe (spi_sdio_oe),
        .step_index  (step_index),
        .last_addr   (last_addr),
        .last_data   (last_data)
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

        if (errors == 0) begin
            $display("HMC7044 SPI TEST PASSED");
        end else begin
            $display("HMC7044 SPI TEST FAILED with %0d errors", errors);
            $fatal(1);
        end
        $finish;
    end
endmodule

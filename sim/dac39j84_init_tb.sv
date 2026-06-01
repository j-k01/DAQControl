`timescale 1ns/1ps

module dac39j84_init_tb;
    reg clk = 1'b0;
    reg rst = 1'b1;
    reg start = 1'b0;
    reg restart = 1'b0;

    wire busy;
    wire done;
    wire reset_n;
    wire spi_cs_n;
    wire spi_sclk;
    wire spi_sdin;
    wire [5:0] step_index;
    wire [7:0] last_addr;
    wire [15:0] last_data;
    wire [31:0] status;
    wire [31:0] last_write;

    dac39j84_init #(
        .CLK_HZ               (1),
        .SPI_HZ               (1_000_000),
        .RESET_ASSERT_US      (0),
        .RESET_RELEASE_US     (0),
        .WRITE_GAP_US         (0),
        .CLEAR_ALARM_DELAY_US (0)
    ) dut (
        .clk        (clk),
        .rst        (rst),
        .start      (start),
        .restart    (restart),
        .busy       (busy),
        .done       (done),
        .reset_n    (reset_n),
        .spi_cs_n   (spi_cs_n),
        .spi_sclk   (spi_sclk),
        .spi_sdin   (spi_sdin),
        .step_index (step_index),
        .last_addr  (last_addr),
        .last_data  (last_data),
        .status     (status),
        .last_write (last_write)
    );

    always #5 clk = ~clk;

    integer errors = 0;
    integer bit_count = 0;
    integer write_count = 0;
    reg [23:0] first_word = 24'd0;
    time sdin_change_time = 0;

    always @(spi_sdin) begin
        sdin_change_time = $time;
    end

    always @(posedge spi_sclk) begin
        time rise_time;
        rise_time = $time;
        #1;
        if (!spi_cs_n && sdin_change_time == rise_time) begin
            $display("FAIL: SDIN changed on SCLK rising edge at %0t", rise_time);
            errors = errors + 1;
        end
        if (!spi_cs_n && bit_count < 24) begin
            first_word = {first_word[22:0], spi_sdin};
            bit_count = bit_count + 1;
        end
    end

    always @(posedge spi_cs_n) begin
        if (busy) begin
            write_count = write_count + 1;
        end
    end

    initial begin
        #25 rst = 1'b0;
        #20 start = 1'b1;

        wait (bit_count == 24);
        #10;
        if (first_word !== 24'h022083) begin
            $display("FAIL: first DAC write expected 0x022083 got 0x%06x", first_word);
            errors = errors + 1;
        end else begin
            $display("PASS: first DAC write = 0x%06x", first_word);
        end

        wait (done);
        #10;
        if (!reset_n) begin
            $display("FAIL: DAC reset_n not released at done");
            errors = errors + 1;
        end
        if (write_count !== 47) begin
            $display("FAIL: DAC write count expected 47 got %0d", write_count);
            errors = errors + 1;
        end
        if (last_write !== 32'h006D0000) begin
            $display("FAIL: last DAC write expected 0x006D0000 got 0x%08x", last_write);
            errors = errors + 1;
        end
        if (status[31:24] !== 8'hD4) begin
            $display("FAIL: DAC status signature expected 0xD4 got 0x%02x", status[31:24]);
            errors = errors + 1;
        end

        if (errors == 0) begin
            $display("DAC39J84 SPI TEST PASSED");
        end else begin
            $display("DAC39J84 SPI TEST FAILED with %0d errors", errors);
            $fatal(1);
        end
        $finish;
    end
endmodule

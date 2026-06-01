`timescale 1ns/1ps

module ads54j60_init_tb;
    reg clk = 1'b0;
    reg rst = 1'b1;
    reg start = 1'b0;
    reg restart = 1'b0;
    reg adc1_sdout = 1'b0;
    reg adc2_sdout = 1'b0;

    wire busy;
    wire done;
    wire adc1_reset;
    wire adc2_reset;
    wire spi_cs1_n;
    wire spi_cs2_n;
    wire spi_sclk;
    wire spi_sdin;
    wire chip_index;
    wire [6:0] op_index;
    wire [15:0] last_addr;
    wire [7:0] last_data;
    wire readback_done1;
    wire readback_done2;
    wire readback_ok1;
    wire readback_ok2;
    wire sdout_stuck1;
    wire sdout_stuck2;
    wire [23:0] adc1_analog_word;
    wire [23:0] adc1_jesd_digital_word;
    wire [15:0] adc1_jesd_analog_word;
    wire [23:0] adc2_analog_word;
    wire [23:0] adc2_jesd_digital_word;
    wire [15:0] adc2_jesd_analog_word;
    wire [15:0] last_read_addr;
    wire [7:0] last_read_data;
    wire last_read_chip;
    wire [31:0] status;
    wire [31:0] last_write;
    wire [31:0] last_read;

    ads54j60_init #(
        .CLK_HZ           (1),
        .SPI_HZ           (1_000_000),
        .RESET_ASSERT_US  (0),
        .RESET_RELEASE_US (0),
        .OP_GAP_US        (0)
    ) dut (
        .clk                    (clk),
        .rst                    (rst),
        .start                  (start),
        .restart                (restart),
        .adc1_sdout             (adc1_sdout),
        .adc2_sdout             (adc2_sdout),
        .busy                   (busy),
        .done                   (done),
        .adc1_reset             (adc1_reset),
        .adc2_reset             (adc2_reset),
        .spi_cs1_n              (spi_cs1_n),
        .spi_cs2_n              (spi_cs2_n),
        .spi_sclk               (spi_sclk),
        .spi_sdin               (spi_sdin),
        .chip_index             (chip_index),
        .op_index               (op_index),
        .last_addr              (last_addr),
        .last_data              (last_data),
        .readback_done1         (readback_done1),
        .readback_done2         (readback_done2),
        .readback_ok1           (readback_ok1),
        .readback_ok2           (readback_ok2),
        .sdout_stuck1           (sdout_stuck1),
        .sdout_stuck2           (sdout_stuck2),
        .adc1_analog_word       (adc1_analog_word),
        .adc1_jesd_digital_word (adc1_jesd_digital_word),
        .adc1_jesd_analog_word  (adc1_jesd_analog_word),
        .adc2_analog_word       (adc2_analog_word),
        .adc2_jesd_digital_word (adc2_jesd_digital_word),
        .adc2_jesd_analog_word  (adc2_jesd_analog_word),
        .last_read_addr         (last_read_addr),
        .last_read_data         (last_read_data),
        .last_read_chip         (last_read_chip),
        .status                 (status),
        .last_write             (last_write),
        .last_read              (last_read)
    );

    always #5 clk = ~clk;

    integer errors = 0;
    integer bit_count = 0;
    integer write_count = 0;
    integer read_count = 0;
    reg [23:0] first_word = 24'd0;
    reg [15:0] command_word = 16'd0;
    reg [7:0] read_value = 8'd0;
    reg active_chip = 1'b0;

    function [7:0] model_read_data;
        input [15:0] command;
        reg [15:0] addr;
        begin
            addr = {command[15:8] & 8'h7F, command[7:0]};
            case (addr)
                16'h0026: model_read_data = 8'h40;
                16'h0059: model_read_data = 8'h20;
                16'h004F: model_read_data = 8'h01;
                16'h6001: model_read_data = 8'h04;
                16'h6005: model_read_data = 8'h80;
                16'h6006: model_read_data = 8'h1F;
                16'h6016: model_read_data = 8'h02;
                16'h6017: model_read_data = 8'h00;
                default:  model_read_data = 8'h5A;
            endcase
        end
    endfunction

    always @(negedge spi_cs1_n or negedge spi_cs2_n) begin
        bit_count = 0;
        command_word = 16'd0;
        read_value = 8'd0;
        active_chip = !spi_cs2_n;
    end

    always @(posedge spi_sclk) begin
        if (!spi_cs1_n || !spi_cs2_n) begin
            if (bit_count < 16) begin
                command_word = {command_word[14:0], spi_sdin};
            end
            if (bit_count < 24 && bit_count < 24) begin
                if (write_count == 0 && read_count == 0 && bit_count < 24) begin
                    first_word = {first_word[22:0], spi_sdin};
                end
            end
            bit_count = bit_count + 1;
        end
    end

    always @(negedge spi_sclk) begin
        if (!spi_cs1_n || !spi_cs2_n) begin
            if (bit_count == 16) begin
                read_value = model_read_data(command_word);
            end
            if (bit_count >= 16 && bit_count < 24) begin
                if (active_chip) begin
                    adc2_sdout = read_value[23 - bit_count];
                end else begin
                    adc1_sdout = read_value[23 - bit_count];
                end
            end
        end
    end

    always @(posedge spi_cs1_n or posedge spi_cs2_n) begin
        if (bit_count == 24) begin
            if (command_word[15]) begin
                read_count = read_count + 1;
            end else begin
                write_count = write_count + 1;
            end
        end
    end

    initial begin
        #25 rst = 1'b0;
        #20 start = 1'b1;

        wait (done);
        #20;

        if (first_word !== 24'h000081) begin
            $display("FAIL: first ADC write expected 0x000081 got 0x%06x", first_word);
            errors = errors + 1;
        end else begin
            $display("PASS: first ADC write = 0x%06x", first_word);
        end
        if (write_count !== 70) begin
            $display("FAIL: ADC write count expected 70 got %0d", write_count);
            errors = errors + 1;
        end
        if (read_count !== 16) begin
            $display("FAIL: ADC read count expected 16 got %0d", read_count);
            errors = errors + 1;
        end
        if (adc1_reset || adc2_reset) begin
            $display("FAIL: ADC resets should be deasserted at done");
            errors = errors + 1;
        end
        if (!readback_done1 || !readback_done2 || !readback_ok1 || !readback_ok2) begin
            $display("FAIL: ADC readback status bad: done=%0b/%0b ok=%0b/%0b",
                     readback_done1, readback_done2, readback_ok1, readback_ok2);
            errors = errors + 1;
        end
        if (sdout_stuck1 || sdout_stuck2) begin
            $display("FAIL: ADC SDOUT stuck flags asserted");
            errors = errors + 1;
        end
        if (adc1_analog_word !== 24'h012040 || adc2_analog_word !== 24'h012040) begin
            $display("FAIL: ADC analog readback words adc1=0x%06x adc2=0x%06x",
                     adc1_analog_word, adc2_analog_word);
            errors = errors + 1;
        end
        if (adc1_jesd_digital_word !== 24'h1F8004 ||
            adc2_jesd_digital_word !== 24'h1F8004) begin
            $display("FAIL: ADC JESD digital readback words adc1=0x%06x adc2=0x%06x",
                     adc1_jesd_digital_word, adc2_jesd_digital_word);
            errors = errors + 1;
        end
        if (adc1_jesd_analog_word !== 16'h0002 ||
            adc2_jesd_analog_word !== 16'h0002) begin
            $display("FAIL: ADC JESD analog readback words adc1=0x%04x adc2=0x%04x",
                     adc1_jesd_analog_word, adc2_jesd_analog_word);
            errors = errors + 1;
        end
        if (last_write !== 32'h01400468) begin
            $display("FAIL: last ADC write expected 0x01400468 got 0x%08x", last_write);
            errors = errors + 1;
        end
        if (last_read !== 32'h01601700) begin
            $display("FAIL: last ADC read expected 0x01601700 got 0x%08x", last_read);
            errors = errors + 1;
        end
        if (status[31:24] !== 8'hAD) begin
            $display("FAIL: ADC status signature expected 0xAD got 0x%02x", status[31:24]);
            errors = errors + 1;
        end

        if (errors == 0) begin
            $display("ADS54J60 SPI TEST PASSED");
        end else begin
            $display("ADS54J60 SPI TEST FAILED with %0d errors", errors);
            $fatal(1);
        end
        $finish;
    end
endmodule

`timescale 1ns/1ps

// Startup-only ADS54J60 SPI loader for the FMC-ADC500-CD ADC path.
//
// This follows the Sundance ads54j60-tool LMFS=4211 sequence. Each ADS54J60
// is a dual-channel converter configured for 4 JESD204B lanes, no decimation,
// scrambling enabled, and K=32. The two ADC chips share SCLK/SDIN and have
// independent chip-select, SDOUT, and active-high RESET pins.
module ads54j60_init #(
    parameter integer CLK_HZ = 200_000_000,
    parameter integer SPI_HZ = 500_000,
    parameter integer RESET_ASSERT_US = 10_000,
    parameter integer RESET_RELEASE_US = 10_000,
    parameter integer OP_GAP_US = 10_000
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        start,
    input  wire        restart,
    input  wire        adc1_sdout,
    input  wire        adc2_sdout,
    output reg         busy,
    output reg         done,
    output reg         adc1_reset,
    output reg         adc2_reset,
    output reg         spi_cs1_n,
    output reg         spi_cs2_n,
    output reg         spi_sclk,
    output reg         spi_sdin,
    output reg         chip_index,
    output reg  [6:0]  op_index,
    output reg  [15:0] last_addr,
    output reg  [7:0]  last_data,
    output reg         readback_done1,
    output reg         readback_done2,
    output reg         readback_ok1,
    output reg         readback_ok2,
    output reg         sdout_stuck1,
    output reg         sdout_stuck2,
    output reg  [23:0] adc1_analog_word,
    output reg  [23:0] adc1_jesd_digital_word,
    output reg  [15:0] adc1_jesd_analog_word,
    output reg  [23:0] adc2_analog_word,
    output reg  [23:0] adc2_jesd_digital_word,
    output reg  [15:0] adc2_jesd_analog_word,
    output reg  [15:0] last_read_addr,
    output reg  [7:0]  last_read_data,
    output reg         last_read_chip,
    output wire [31:0] status,
    output wire [31:0] last_write,
    output wire [31:0] last_read
);

    localparam integer US_CYCLES = (CLK_HZ + 999_999) / 1_000_000;
    localparam integer RESET_ASSERT_CYCLES = RESET_ASSERT_US * US_CYCLES;
    localparam integer RESET_RELEASE_CYCLES = RESET_RELEASE_US * US_CYCLES;
    localparam integer OP_GAP_CYCLES = OP_GAP_US * US_CYCLES;
    localparam integer SPI_HALF_CYCLES = (CLK_HZ + (SPI_HZ * 2) - 1) / (SPI_HZ * 2);

    localparam [3:0]
        ST_IDLE          = 4'd0,
        ST_RESET_ASSERT  = 4'd1,
        ST_RESET_RELEASE = 4'd2,
        ST_LOAD          = 4'd3,
        ST_SPI           = 4'd4,
        ST_GAP           = 4'd5,
        ST_NEXT_CHIP     = 4'd6,
        ST_DONE          = 4'd7;

    localparam [6:0] INIT_LEN   = 7'd28;
    localparam [6:0] VERIFY_LEN = 7'd15;
    localparam [6:0] OP_LEN     = INIT_LEN + VERIFY_LEN;

    reg [3:0]  state = ST_IDLE;
    reg [31:0] delay_count = 32'd0;
    reg [31:0] spi_div_count = 32'd0;
    reg [23:0] spi_shift = 24'd0;
    reg [5:0]  spi_bits_left = 6'd0;
    reg        spi_half_phase = 1'b0;
    reg        op_read_mode = 1'b0;
    reg [7:0]  spi_read_shift = 8'd0;
    reg [7:0]  expected_read_data = 8'd0;
    reg        expected_read_valid = 1'b0;
    reg [7:0]  read_nonzero_or = 8'd0;
    reg [7:0]  read_allones_and = 8'hff;
    reg        chip_ok_accum = 1'b0;

    wire [6:0] verify_index = op_index - INIT_LEN;
    wire       seq_is_verify = (op_index >= INIT_LEN);
    wire       seq_is_read = seq_is_verify && verify_op_is_read(verify_index);
    wire [15:0] seq_addr = seq_is_verify ? verify_op_addr(verify_index) :
                                            init_reg_addr(op_index);
    wire [7:0] seq_data = seq_is_verify ? verify_op_data(verify_index) :
                                          init_reg_data(op_index);
    wire [7:0] seq_expected = verify_op_expected(verify_index);
    wire       active_sdout = chip_index ? adc2_sdout : adc1_sdout;
    wire [7:0] spi_cmd_hi = seq_is_read ? (seq_addr[15:8] | 8'h80) :
                                           seq_addr[15:8];

    assign status = {
        8'hAD,
        done,
        busy,
        readback_ok1,
        readback_ok2,
        sdout_stuck1,
        sdout_stuck2,
        adc1_reset,
        adc2_reset,
        spi_cs1_n,
        spi_cs2_n,
        spi_sclk,
        spi_sdin,
        chip_index,
        state,
        op_index
    };

    assign last_write = {
        7'd0,
        chip_index,
        last_addr,
        last_data
    };

    assign last_read = {
        7'd0,
        last_read_chip,
        last_read_addr,
        last_read_data
    };

    function [15:0] init_reg_addr;
        input [6:0] idx;
        begin
            case (idx)
                7'd0:  init_reg_addr = 16'h0000;
                7'd1:  init_reg_addr = 16'h4001;
                7'd2:  init_reg_addr = 16'h4002;
                7'd3:  init_reg_addr = 16'h4003;
                7'd4:  init_reg_addr = 16'h4004;
                7'd5:  init_reg_addr = 16'h60F7;
                7'd6:  init_reg_addr = 16'h6000;
                7'd7:  init_reg_addr = 16'h6000;
                7'd8:  init_reg_addr = 16'h0011;
                7'd9:  init_reg_addr = 16'h0026;
                7'd10: init_reg_addr = 16'h0059;
                7'd11: init_reg_addr = 16'h004F;
                7'd12: init_reg_addr = 16'h4003;
                7'd13: init_reg_addr = 16'h4004;
                7'd14: init_reg_addr = 16'h6000;
                7'd15: init_reg_addr = 16'h6001;
                7'd16: init_reg_addr = 16'h6005;
                7'd17: init_reg_addr = 16'h6006;
                7'd18: init_reg_addr = 16'h4003;
                7'd19: init_reg_addr = 16'h4004;
                7'd20: init_reg_addr = 16'h6016;
                7'd21: init_reg_addr = 16'h6017;
                7'd22: init_reg_addr = 16'h6017;
                7'd23: init_reg_addr = 16'h4003;
                7'd24: init_reg_addr = 16'h4004;
                7'd25: init_reg_addr = 16'h6000;
                7'd26: init_reg_addr = 16'h6000;
                default: init_reg_addr = 16'h0000;
            endcase
        end
    endfunction

    function [7:0] init_reg_data;
        input [6:0] idx;
        begin
            case (idx)
                7'd0:  init_reg_data = 8'h81;
                7'd1:  init_reg_data = 8'h00;
                7'd2:  init_reg_data = 8'h00;
                7'd3:  init_reg_data = 8'h00;
                7'd4:  init_reg_data = 8'h68;
                7'd5:  init_reg_data = 8'h01;
                7'd6:  init_reg_data = 8'h01;
                7'd7:  init_reg_data = 8'h00;
                7'd8:  init_reg_data = 8'h80;
                7'd9:  init_reg_data = 8'h40;
                7'd10: init_reg_data = 8'h20;
                7'd11: init_reg_data = 8'h01;
                7'd12: init_reg_data = 8'h00;
                7'd13: init_reg_data = 8'h69;
                7'd14: init_reg_data = 8'h80;
                7'd15: init_reg_data = 8'h04;
                7'd16: init_reg_data = 8'h80;
                7'd17: init_reg_data = 8'h1F;
                7'd18: init_reg_data = 8'h00;
                7'd19: init_reg_data = 8'h6A;
                7'd20: init_reg_data = 8'h02;
                7'd21: init_reg_data = 8'h40;
                7'd22: init_reg_data = 8'h00;
                7'd23: init_reg_data = 8'h00;
                7'd24: init_reg_data = 8'h68;
                7'd25: init_reg_data = 8'h01;
                7'd26: init_reg_data = 8'h00;
                default: init_reg_data = 8'h00;
            endcase
        end
    endfunction

    function verify_op_is_read;
        input [6:0] idx;
        begin
            case (idx)
                7'd1, 7'd2, 7'd3,
                7'd6, 7'd7, 7'd8,
                7'd11, 7'd12: verify_op_is_read = 1'b1;
                default: verify_op_is_read = 1'b0;
            endcase
        end
    endfunction

    function [15:0] verify_op_addr;
        input [6:0] idx;
        begin
            case (idx)
                7'd0:  verify_op_addr = 16'h0011; // analog master page
                7'd1:  verify_op_addr = 16'h0026;
                7'd2:  verify_op_addr = 16'h0059;
                7'd3:  verify_op_addr = 16'h004F;
                7'd4:  verify_op_addr = 16'h4003; // JESD digital page
                7'd5:  verify_op_addr = 16'h4004;
                7'd6:  verify_op_addr = 16'h6001;
                7'd7:  verify_op_addr = 16'h6005;
                7'd8:  verify_op_addr = 16'h6006;
                7'd9:  verify_op_addr = 16'h4003; // JESD analog page
                7'd10: verify_op_addr = 16'h4004;
                7'd11: verify_op_addr = 16'h6016;
                7'd12: verify_op_addr = 16'h6017;
                7'd13: verify_op_addr = 16'h4003; // restore main digital page
                7'd14: verify_op_addr = 16'h4004;
                default: verify_op_addr = 16'h0000;
            endcase
        end
    endfunction

    function [7:0] verify_op_data;
        input [6:0] idx;
        begin
            case (idx)
                7'd0:  verify_op_data = 8'h80;
                7'd4:  verify_op_data = 8'h00;
                7'd5:  verify_op_data = 8'h69;
                7'd9:  verify_op_data = 8'h00;
                7'd10: verify_op_data = 8'h6A;
                7'd13: verify_op_data = 8'h00;
                7'd14: verify_op_data = 8'h68;
                default: verify_op_data = 8'h00;
            endcase
        end
    endfunction

    function [7:0] verify_op_expected;
        input [6:0] idx;
        begin
            case (idx)
                7'd1:  verify_op_expected = 8'h40;
                7'd2:  verify_op_expected = 8'h20;
                7'd3:  verify_op_expected = 8'h01;
                7'd6:  verify_op_expected = 8'h04;
                7'd7:  verify_op_expected = 8'h80;
                7'd8:  verify_op_expected = 8'h1F;
                7'd11: verify_op_expected = 8'h02;
                7'd12: verify_op_expected = 8'h00;
                default: verify_op_expected = 8'h00;
            endcase
        end
    endfunction

    task clear_readback_words;
        begin
            readback_done1 <= 1'b0;
            readback_done2 <= 1'b0;
            readback_ok1 <= 1'b0;
            readback_ok2 <= 1'b0;
            sdout_stuck1 <= 1'b0;
            sdout_stuck2 <= 1'b0;
            adc1_analog_word <= 24'd0;
            adc1_jesd_digital_word <= 24'd0;
            adc1_jesd_analog_word <= 16'd0;
            adc2_analog_word <= 24'd0;
            adc2_jesd_digital_word <= 24'd0;
            adc2_jesd_analog_word <= 16'd0;
            last_read_addr <= 16'd0;
            last_read_data <= 8'd0;
            last_read_chip <= 1'b0;
        end
    endtask

    task reset_spi_state;
        begin
            spi_cs1_n <= 1'b1;
            spi_cs2_n <= 1'b1;
            spi_sclk <= 1'b0;
            spi_sdin <= 1'b0;
            delay_count <= 32'd0;
            spi_div_count <= 32'd0;
            spi_shift <= 24'd0;
            spi_bits_left <= 6'd0;
            spi_half_phase <= 1'b0;
            op_read_mode <= 1'b0;
            spi_read_shift <= 8'd0;
            expected_read_data <= 8'd0;
            expected_read_valid <= 1'b0;
            read_nonzero_or <= 8'd0;
            read_allones_and <= 8'hff;
            chip_ok_accum <= 1'b1;
        end
    endtask

    always @(posedge clk) begin
        if (rst) begin
            state <= ST_IDLE;
            busy <= 1'b0;
            done <= 1'b0;
            adc1_reset <= 1'b1;
            adc2_reset <= 1'b1;
            chip_index <= 1'b0;
            op_index <= 7'd0;
            last_addr <= 16'd0;
            last_data <= 8'd0;
            clear_readback_words();
            reset_spi_state();
        end else if (restart) begin
            state <= ST_RESET_ASSERT;
            busy <= 1'b1;
            done <= 1'b0;
            adc1_reset <= 1'b1;
            adc2_reset <= 1'b1;
            chip_index <= 1'b0;
            op_index <= 7'd0;
            last_addr <= 16'd0;
            last_data <= 8'd0;
            clear_readback_words();
            reset_spi_state();
            delay_count <= RESET_ASSERT_CYCLES[31:0];
        end else begin
            case (state)
                ST_IDLE: begin
                    busy <= 1'b0;
                    done <= 1'b0;
                    adc1_reset <= 1'b1;
                    adc2_reset <= 1'b1;
                    spi_cs1_n <= 1'b1;
                    spi_cs2_n <= 1'b1;
                    spi_sclk <= 1'b0;
                    spi_sdin <= 1'b0;
                    chip_index <= 1'b0;
                    op_index <= 7'd0;
                    if (start) begin
                        busy <= 1'b1;
                        clear_readback_words();
                        read_nonzero_or <= 8'd0;
                        read_allones_and <= 8'hff;
                        chip_ok_accum <= 1'b1;
                        delay_count <= RESET_ASSERT_CYCLES[31:0];
                        state <= ST_RESET_ASSERT;
                    end
                end

                ST_RESET_ASSERT: begin
                    adc1_reset <= 1'b1;
                    adc2_reset <= 1'b1;
                    if (delay_count == 32'd0) begin
                        adc1_reset <= 1'b0;
                        adc2_reset <= 1'b0;
                        delay_count <= RESET_RELEASE_CYCLES[31:0];
                        state <= ST_RESET_RELEASE;
                    end else begin
                        delay_count <= delay_count - 1'b1;
                    end
                end

                ST_RESET_RELEASE: begin
                    if (delay_count == 32'd0) begin
                        op_index <= 7'd0;
                        chip_index <= 1'b0;
                        read_nonzero_or <= 8'd0;
                        read_allones_and <= 8'hff;
                        chip_ok_accum <= 1'b1;
                        state <= ST_LOAD;
                    end else begin
                        delay_count <= delay_count - 1'b1;
                    end
                end

                ST_LOAD: begin
                    spi_sclk <= 1'b0;
                    spi_cs1_n <= 1'b1;
                    spi_cs2_n <= 1'b1;
                    if (op_index >= OP_LEN) begin
                        state <= ST_NEXT_CHIP;
                    end else begin
                        last_addr <= seq_addr;
                        if (!seq_is_read) begin
                            last_data <= seq_data;
                        end
                        op_read_mode <= seq_is_read;
                        expected_read_data <= seq_expected;
                        expected_read_valid <= seq_is_read;
                        spi_shift <= {spi_cmd_hi, seq_addr[7:0], seq_is_read ? 8'h00 : seq_data};
                        spi_bits_left <= 6'd24;
                        spi_half_phase <= 1'b0;
                        spi_read_shift <= 8'd0;
                        spi_div_count <= SPI_HALF_CYCLES[31:0] - 1'b1;
                        if (chip_index) begin
                            spi_cs2_n <= 1'b0;
                        end else begin
                            spi_cs1_n <= 1'b0;
                        end
                        spi_sdin <= spi_cmd_hi[7];
                        state <= ST_SPI;
                    end
                end

                ST_SPI: begin
                    if (spi_div_count != 32'd0) begin
                        spi_div_count <= spi_div_count - 1'b1;
                    end else begin
                        spi_div_count <= SPI_HALF_CYCLES[31:0] - 1'b1;
                        if (!spi_half_phase) begin
                            spi_sclk <= 1'b1;
                            spi_half_phase <= 1'b1;
                            if (op_read_mode && (spi_bits_left <= 6'd8)) begin
                                spi_read_shift <= {spi_read_shift[6:0], active_sdout};
                            end
                        end else begin
                            spi_sclk <= 1'b0;
                            spi_half_phase <= 1'b0;
                            if (spi_bits_left == 6'd1) begin
                                spi_cs1_n <= 1'b1;
                                spi_cs2_n <= 1'b1;
                                if (op_read_mode) begin
                                    last_read_chip <= chip_index;
                                    last_read_addr <= seq_addr;
                                    last_read_data <= spi_read_shift;
                                    read_nonzero_or <= read_nonzero_or | spi_read_shift;
                                    read_allones_and <= read_allones_and & spi_read_shift;
                                    if (expected_read_valid &&
                                        (spi_read_shift != expected_read_data)) begin
                                        chip_ok_accum <= 1'b0;
                                    end

                                    if (!chip_index) begin
                                        case (verify_index)
                                            7'd1:  adc1_analog_word[7:0] <= spi_read_shift;
                                            7'd2:  adc1_analog_word[15:8] <= spi_read_shift;
                                            7'd3:  adc1_analog_word[23:16] <= spi_read_shift;
                                            7'd6:  adc1_jesd_digital_word[7:0] <= spi_read_shift;
                                            7'd7:  adc1_jesd_digital_word[15:8] <= spi_read_shift;
                                            7'd8:  adc1_jesd_digital_word[23:16] <= spi_read_shift;
                                            7'd11: adc1_jesd_analog_word[7:0] <= spi_read_shift;
                                            7'd12: adc1_jesd_analog_word[15:8] <= spi_read_shift;
                                            default: begin end
                                        endcase
                                    end else begin
                                        case (verify_index)
                                            7'd1:  adc2_analog_word[7:0] <= spi_read_shift;
                                            7'd2:  adc2_analog_word[15:8] <= spi_read_shift;
                                            7'd3:  adc2_analog_word[23:16] <= spi_read_shift;
                                            7'd6:  adc2_jesd_digital_word[7:0] <= spi_read_shift;
                                            7'd7:  adc2_jesd_digital_word[15:8] <= spi_read_shift;
                                            7'd8:  adc2_jesd_digital_word[23:16] <= spi_read_shift;
                                            7'd11: adc2_jesd_analog_word[7:0] <= spi_read_shift;
                                            7'd12: adc2_jesd_analog_word[15:8] <= spi_read_shift;
                                            default: begin end
                                        endcase
                                    end
                                end
                                op_index <= op_index + 1'b1;
                                delay_count <= OP_GAP_CYCLES[31:0];
                                state <= ST_GAP;
                            end else begin
                                spi_sdin <= spi_shift[22];
                                spi_shift <= {spi_shift[22:0], 1'b0};
                                spi_bits_left <= spi_bits_left - 1'b1;
                            end
                        end
                    end
                end

                ST_GAP: begin
                    if (delay_count == 32'd0) begin
                        state <= ST_LOAD;
                    end else begin
                        delay_count <= delay_count - 1'b1;
                    end
                end

                ST_NEXT_CHIP: begin
                    if (!chip_index) begin
                        readback_done1 <= 1'b1;
                        sdout_stuck1 <= (read_nonzero_or == 8'h00) ||
                                        (read_allones_and == 8'hff);
                        readback_ok1 <= chip_ok_accum &&
                                        (read_nonzero_or != 8'h00) &&
                                        (read_allones_and != 8'hff);
                        chip_index <= 1'b1;
                        op_index <= 7'd0;
                        read_nonzero_or <= 8'd0;
                        read_allones_and <= 8'hff;
                        chip_ok_accum <= 1'b1;
                        state <= ST_LOAD;
                    end else begin
                        readback_done2 <= 1'b1;
                        sdout_stuck2 <= (read_nonzero_or == 8'h00) ||
                                        (read_allones_and == 8'hff);
                        readback_ok2 <= chip_ok_accum &&
                                        (read_nonzero_or != 8'h00) &&
                                        (read_allones_and != 8'hff);
                        state <= ST_DONE;
                    end
                end

                ST_DONE: begin
                    busy <= 1'b0;
                    done <= 1'b1;
                    adc1_reset <= 1'b0;
                    adc2_reset <= 1'b0;
                    spi_cs1_n <= 1'b1;
                    spi_cs2_n <= 1'b1;
                    spi_sclk <= 1'b0;
                    spi_sdin <= 1'b0;
                end

                default: begin
                    state <= ST_IDLE;
                end
            endcase
        end
    end

endmodule

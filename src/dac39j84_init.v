`timescale 1ns/1ps

// Startup-only DAC39J84 SPI loader for the FMC-ADC500-CD DAC path.
//
// This follows the Sundance init8411_dac_remapped sequence: one JESD204B
// link, 8 lanes, 4 DAC converters, F=1, S=1, HD=1, with DAC-side lane
// remapping.  One register differs intentionally from the Sundance table:
// config77 / 0x4D is programmed as 0x0300 because the TI DAC39J84 data
// sheet defines bits [15:8] as M-1 and bits [4:0] as S-1.  For LMF=841,
// M=4 and S=1, so M-1=3 and S-1=0.
module dac39j84_init #(
    parameter integer CLK_HZ = 200_000_000,
    parameter integer SPI_HZ = 500_000,
    parameter integer RESET_ASSERT_US = 10_000,
    parameter integer RESET_RELEASE_US = 10_000,
    parameter integer WRITE_GAP_US = 10_000,
    parameter integer CLEAR_ALARM_DELAY_US = 1_000_000
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        start,
    input  wire        restart,
    output reg         busy,
    output reg         done,
    output reg         reset_n,
    output reg         spi_cs_n,
    output reg         spi_sclk,
    output reg         spi_sdin,
    output reg  [5:0]  step_index,
    output reg  [7:0]  last_addr,
    output reg  [15:0] last_data,
    output wire [31:0] status,
    output wire [31:0] last_write
);

    localparam integer US_CYCLES = (CLK_HZ + 999_999) / 1_000_000;
    localparam integer RESET_ASSERT_CYCLES = RESET_ASSERT_US * US_CYCLES;
    localparam integer RESET_RELEASE_CYCLES = RESET_RELEASE_US * US_CYCLES;
    localparam integer WRITE_GAP_CYCLES = WRITE_GAP_US * US_CYCLES;
    localparam integer CLEAR_ALARM_DELAY_CYCLES = CLEAR_ALARM_DELAY_US * US_CYCLES;
    localparam integer SPI_HALF_CYCLES = (CLK_HZ + (SPI_HZ * 2) - 1) / (SPI_HZ * 2);

    localparam [3:0]
        ST_IDLE          = 4'd0,
        ST_RESET_ASSERT  = 4'd1,
        ST_RESET_RELEASE = 4'd2,
        ST_LOAD          = 4'd3,
        ST_PRE_DELAY     = 4'd4,
        ST_SPI           = 4'd5,
        ST_WRITE_DELAY   = 4'd6,
        ST_DONE          = 4'd7;

    localparam [5:0] SEQ_LEN = 6'd47;

    reg [3:0]  state = ST_IDLE;
    reg [31:0] delay_count = 32'd0;
    reg [31:0] spi_div_count = 32'd0;
    reg [23:0] spi_shift = 24'd0;
    reg [5:0]  spi_bits_left = 6'd0;
    reg        spi_half_phase = 1'b0;
    reg        pre_delay_done = 1'b0;

    wire       seq_pre_delay = (seq_reg_addr(step_index) == 8'h64);
    wire [7:0] seq_addr = seq_reg_addr(step_index);
    wire [15:0] seq_data = seq_reg_data(step_index);

    assign status = {
        8'hD4,
        done,
        busy,
        reset_n,
        spi_cs_n,
        spi_sclk,
        spi_sdin,
        2'd0,
        state,
        6'd0,
        step_index
    };

    assign last_write = {
        8'd0,
        last_addr,
        last_data
    };

    function [7:0] seq_reg_addr;
        input [5:0] idx;
        begin
            case (idx)
                6'd0:  seq_reg_addr = 8'h02;
                6'd1:  seq_reg_addr = 8'h02;
                6'd2:  seq_reg_addr = 8'h03;
                6'd3:  seq_reg_addr = 8'h1A;
                6'd4:  seq_reg_addr = 8'h1B;
                6'd5:  seq_reg_addr = 8'h31;
                6'd6:  seq_reg_addr = 8'h3B;
                6'd7:  seq_reg_addr = 8'h3C;
                6'd8:  seq_reg_addr = 8'h3D;
                6'd9:  seq_reg_addr = 8'h3E;
                6'd10: seq_reg_addr = 8'h3F;
                6'd11: seq_reg_addr = 8'h46;
                6'd12: seq_reg_addr = 8'h47;
                6'd13: seq_reg_addr = 8'h48;
                6'd14: seq_reg_addr = 8'h49;
                6'd15: seq_reg_addr = 8'h4A;
                6'd16: seq_reg_addr = 8'h5F;
                6'd17: seq_reg_addr = 8'h60;
                6'd18: seq_reg_addr = 8'h25;
                6'd19: seq_reg_addr = 8'h24;
                6'd20: seq_reg_addr = 8'h00;
                6'd21: seq_reg_addr = 8'h03;
                6'd22: seq_reg_addr = 8'h4B;
                6'd23: seq_reg_addr = 8'h4C;
                6'd24: seq_reg_addr = 8'h4D;
                6'd25: seq_reg_addr = 8'h4E;
                6'd26: seq_reg_addr = 8'h4F;
                6'd27: seq_reg_addr = 8'h51;
                6'd28: seq_reg_addr = 8'h54;
                6'd29: seq_reg_addr = 8'h50;
                6'd30: seq_reg_addr = 8'h52;
                6'd31: seq_reg_addr = 8'h53;
                6'd32: seq_reg_addr = 8'h55;
                6'd33: seq_reg_addr = 8'h5C;
                6'd34: seq_reg_addr = 8'h61;
                6'd35: seq_reg_addr = 8'h4A;
                6'd36: seq_reg_addr = 8'h03;
                6'd37: seq_reg_addr = 8'h64;
                6'd38: seq_reg_addr = 8'h65;
                6'd39: seq_reg_addr = 8'h66;
                6'd40: seq_reg_addr = 8'h67;
                6'd41: seq_reg_addr = 8'h68;
                6'd42: seq_reg_addr = 8'h69;
                6'd43: seq_reg_addr = 8'h6A;
                6'd44: seq_reg_addr = 8'h6B;
                6'd45: seq_reg_addr = 8'h6C;
                6'd46: seq_reg_addr = 8'h6D;
                default: seq_reg_addr = 8'h00;
            endcase
        end
    endfunction

    function [15:0] seq_reg_data;
        input [5:0] idx;
        begin
            case (idx)
                6'd0:  seq_reg_data = 16'h2083;
                6'd1:  seq_reg_data = 16'h2082;
                6'd2:  seq_reg_data = 16'h9380;
                6'd3:  seq_reg_data = 16'h0020;
                6'd4:  seq_reg_data = 16'h0000;
                6'd5:  seq_reg_data = 16'h7030;
                6'd6:  seq_reg_data = 16'h0800;
                6'd7:  seq_reg_data = 16'h8028;
                6'd8:  seq_reg_data = 16'h0008;
                6'd9:  seq_reg_data = 16'h0108;
                6'd10: seq_reg_data = 16'h0000;
                6'd11: seq_reg_data = 16'h1804;
                6'd12: seq_reg_data = 16'h090A;
                6'd13: seq_reg_data = 16'h31C3;
                6'd14: seq_reg_data = 16'h0000;
                6'd15: seq_reg_data = 16'hFF1E;
                6'd16: seq_reg_data = 16'h3021;
                6'd17: seq_reg_data = 16'h7654;
                6'd18: seq_reg_data = 16'h2000;
                6'd19: seq_reg_data = 16'h0040;
                6'd20: seq_reg_data = 16'h0008;
                6'd21: seq_reg_data = 16'h9380;
                6'd22: seq_reg_data = 16'h1F00;
                6'd23: seq_reg_data = 16'h1F07;
                6'd24: seq_reg_data = 16'h0300;
                6'd25: seq_reg_data = 16'h0F6F;
                6'd26: seq_reg_data = 16'h1CE1;
                6'd27: seq_reg_data = 16'h00FF;
                6'd28: seq_reg_data = 16'h00FF;
                6'd29: seq_reg_data = 16'h0000;
                6'd30: seq_reg_data = 16'h00FF;
                6'd31: seq_reg_data = 16'h0000;
                6'd32: seq_reg_data = 16'h00FF;
                6'd33: seq_reg_data = 16'h11EE;
                6'd34: seq_reg_data = 16'h0001;
                6'd35: seq_reg_data = 16'hFF01;
                6'd36: seq_reg_data = 16'h9381;
                6'd37: seq_reg_data = 16'h0000;
                6'd38: seq_reg_data = 16'h0000;
                6'd39: seq_reg_data = 16'h0000;
                6'd40: seq_reg_data = 16'h0000;
                6'd41: seq_reg_data = 16'h0000;
                6'd42: seq_reg_data = 16'h0000;
                6'd43: seq_reg_data = 16'h0000;
                6'd44: seq_reg_data = 16'h0000;
                6'd45: seq_reg_data = 16'h0000;
                6'd46: seq_reg_data = 16'h0000;
                default: seq_reg_data = 16'h0000;
            endcase
        end
    endfunction

    always @(posedge clk) begin
        if (rst) begin
            state <= ST_IDLE;
            busy <= 1'b0;
            done <= 1'b0;
            reset_n <= 1'b0;
            spi_cs_n <= 1'b1;
            spi_sclk <= 1'b0;
            spi_sdin <= 1'b0;
            step_index <= 6'd0;
            last_addr <= 8'd0;
            last_data <= 16'd0;
            delay_count <= 32'd0;
            spi_div_count <= 32'd0;
            spi_shift <= 24'd0;
            spi_bits_left <= 6'd0;
            spi_half_phase <= 1'b0;
            pre_delay_done <= 1'b0;
        end else if (restart) begin
            state <= ST_RESET_ASSERT;
            busy <= 1'b1;
            done <= 1'b0;
            reset_n <= 1'b0;
            spi_cs_n <= 1'b1;
            spi_sclk <= 1'b0;
            spi_sdin <= 1'b0;
            step_index <= 6'd0;
            delay_count <= RESET_ASSERT_CYCLES[31:0];
            spi_div_count <= 32'd0;
            spi_shift <= 24'd0;
            spi_bits_left <= 6'd0;
            spi_half_phase <= 1'b0;
            pre_delay_done <= 1'b0;
        end else begin
            case (state)
                ST_IDLE: begin
                    busy <= 1'b0;
                    done <= 1'b0;
                    reset_n <= 1'b0;
                    spi_cs_n <= 1'b1;
                    spi_sclk <= 1'b0;
                    spi_sdin <= 1'b0;
                    step_index <= 6'd0;
                    pre_delay_done <= 1'b0;
                    if (start) begin
                        busy <= 1'b1;
                        delay_count <= RESET_ASSERT_CYCLES[31:0];
                        state <= ST_RESET_ASSERT;
                    end
                end

                ST_RESET_ASSERT: begin
                    reset_n <= 1'b0;
                    if (delay_count == 32'd0) begin
                        reset_n <= 1'b1;
                        delay_count <= RESET_RELEASE_CYCLES[31:0];
                        state <= ST_RESET_RELEASE;
                    end else begin
                        delay_count <= delay_count - 1'b1;
                    end
                end

                ST_RESET_RELEASE: begin
                    if (delay_count == 32'd0) begin
                        state <= ST_LOAD;
                    end else begin
                        delay_count <= delay_count - 1'b1;
                    end
                end

                ST_LOAD: begin
                    spi_sclk <= 1'b0;
                    spi_cs_n <= 1'b1;
                    if (step_index >= SEQ_LEN) begin
                        state <= ST_DONE;
                    end else if (seq_pre_delay && !pre_delay_done) begin
                        delay_count <= CLEAR_ALARM_DELAY_CYCLES[31:0];
                        pre_delay_done <= 1'b1;
                        state <= ST_PRE_DELAY;
                    end else begin
                        last_addr <= seq_addr;
                        last_data <= seq_data;
                        spi_shift <= {seq_addr, seq_data};
                        spi_bits_left <= 6'd24;
                        spi_half_phase <= 1'b0;
                        spi_div_count <= SPI_HALF_CYCLES[31:0] - 1'b1;
                        spi_cs_n <= 1'b0;
                        spi_sdin <= seq_addr[7];
                        state <= ST_SPI;
                    end
                end

                ST_PRE_DELAY: begin
                    if (delay_count == 32'd0) begin
                        state <= ST_LOAD;
                    end else begin
                        delay_count <= delay_count - 1'b1;
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
                        end else begin
                            spi_sclk <= 1'b0;
                            spi_half_phase <= 1'b0;
                            if (spi_bits_left == 6'd1) begin
                                spi_cs_n <= 1'b1;
                                step_index <= step_index + 1'b1;
                                pre_delay_done <= 1'b0;
                                delay_count <= WRITE_GAP_CYCLES[31:0];
                                state <= ST_WRITE_DELAY;
                            end else begin
                                spi_sdin <= spi_shift[22];
                                spi_shift <= {spi_shift[22:0], 1'b0};
                                spi_bits_left <= spi_bits_left - 1'b1;
                            end
                        end
                    end
                end

                ST_WRITE_DELAY: begin
                    if (delay_count == 32'd0) begin
                        state <= ST_LOAD;
                    end else begin
                        delay_count <= delay_count - 1'b1;
                    end
                end

                ST_DONE: begin
                    busy <= 1'b0;
                    done <= 1'b1;
                    reset_n <= 1'b1;
                    spi_cs_n <= 1'b1;
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

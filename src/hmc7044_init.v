`timescale 1ns/1ps

// Startup-only HMC7044 SPI loader for the FMC-ADC500-CD clock tree.
//
// The register sequence is the Sundance/ADI profile from the FMC-ADC500-CD BSP
// device tree, reduced to write-only SPI transactions using the ADI no-OS
// HMC7044 setup order. It creates:
//   CLK_FMC_GBT1 = 125 MHz, CLK_FMC_GBT0 = 125 MHz,
//   CLK_FMC = 100 MHz, SYSREF_FMC = 3.90625 MHz,
// plus the ADC/DAC sample-clock and SYSREF outputs used by the card.
module hmc7044_init #(
    parameter integer CLK_HZ = 200_000_000,
    parameter integer SPI_HZ = 1_000_000,
    parameter integer RESET_ASSERT_US = 10_000,
    parameter integer RESET_RELEASE_US = 10_000
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        spi_sdio_i,
    output reg         busy,
    output reg         done,
    output reg         reset_out,
    output reg         spi_cs_n,
    output reg         spi_sclk,
    output reg         spi_sdio_o,
    output reg         spi_sdio_oe,
    output reg  [7:0]  step_index,
    output reg  [11:0] last_addr,
    output reg  [7:0]  last_data,
    output reg         readback_done,
    output reg         readback_sdio_stuck,
    output reg  [3:0]  readback_index,
    output reg  [11:0] readback_last_addr,
    output reg  [7:0]  readback_last_data,
    output reg  [31:0] readback_id_word,
    output reg  [31:0] readback_alarm_word,
    output reg  [31:0] readback_pll1_word,
    output reg  [31:0] readback_pll2_word
);

    localparam integer US_CYCLES = (CLK_HZ + 999_999) / 1_000_000;
    localparam integer RESET_ASSERT_CYCLES = RESET_ASSERT_US * US_CYCLES;
    localparam integer RESET_RELEASE_CYCLES = RESET_RELEASE_US * US_CYCLES;
    localparam integer SPI_HALF_CYCLES = (CLK_HZ + (SPI_HZ * 2) - 1) / (SPI_HZ * 2);

    localparam [2:0]
        ST_IDLE          = 3'd0,
        ST_RESET_ASSERT  = 3'd1,
        ST_RESET_RELEASE = 3'd2,
        ST_LOAD          = 3'd3,
        ST_SPI           = 3'd4,
        ST_DELAY         = 3'd5,
        ST_READ_LOAD     = 3'd6,
        ST_DONE          = 3'd7;

    localparam [7:0] SEQ_LEN = 8'd127;
    localparam [3:0] READ_LEN = 4'd13;

    reg [2:0]  state = ST_IDLE;
    reg [31:0] delay_count = 32'd0;
    reg [31:0] spi_div_count = 32'd0;
    reg [23:0] spi_shift = 24'd0;
    reg [5:0]  spi_bits_left = 6'd0;
    reg        spi_half_phase = 1'b0;
    reg        spi_read_mode = 1'b0;
    reg [7:0]  spi_read_shift = 8'd0;

    wire       seq_delay;
    wire [11:0] seq_addr;
    wire [7:0]  seq_data;
    wire [31:0] seq_delay_cycles;

    assign seq_delay = seq_is_delay(step_index);
    assign seq_addr = seq_reg_addr(step_index);
    assign seq_data = seq_reg_data(step_index);
    assign seq_delay_cycles = seq_delay_us(step_index) * US_CYCLES;

    wire [11:0] read_addr = readback_reg_addr(readback_index);

    function [11:0] readback_reg_addr;
        input [3:0] idx;
        begin
            case (idx)
                4'd0:  readback_reg_addr = 12'h078; // Product ID LSB
                4'd1:  readback_reg_addr = 12'h079; // Product ID mid
                4'd2:  readback_reg_addr = 12'h07A; // Product ID MSB
                4'd3:  readback_reg_addr = 12'h07B; // Alarm signal
                4'd4:  readback_reg_addr = 12'h07C; // PLL1 alarm readback
                4'd5:  readback_reg_addr = 12'h07D; // Combined alarm readback
                4'd6:  readback_reg_addr = 12'h07E; // Latched alarm readback
                4'd7:  readback_reg_addr = 12'h082; // PLL1 best/active/FSM
                4'd8:  readback_reg_addr = 12'h083; // PLL1 holdover average
                4'd9:  readback_reg_addr = 12'h084; // PLL1 holdover current
                4'd10: readback_reg_addr = 12'h085; // PLL1 LOS/VCXO status
                4'd11: readback_reg_addr = 12'h08C; // PLL2 autotune value
                4'd12: readback_reg_addr = 12'h08F; // PLL2/SYSREF FSM state
                default: readback_reg_addr = 12'h000;
            endcase
        end
    endfunction

    function seq_is_delay;
        input [7:0] idx;
        begin
            case (idx)
                8'd2, 8'd47, 8'd117, 8'd120, 8'd122, 8'd124, 8'd126: seq_is_delay = 1'b1;
                default: seq_is_delay = 1'b0;
            endcase
        end
    endfunction

    function [31:0] seq_delay_us;
        input [7:0] idx;
        begin
            case (idx)
                8'd2:   seq_delay_us = 32'd100;
                8'd47:  seq_delay_us = 32'd10_000;
                8'd117: seq_delay_us = 32'd10_000;
                8'd120: seq_delay_us = 32'd1_000;
                8'd122: seq_delay_us = 32'd1_000;
                8'd124: seq_delay_us = 32'd100;
                8'd126: seq_delay_us = 32'd10_000;
                default: seq_delay_us = 32'd0;
            endcase
        end
    endfunction

    function [11:0] seq_reg_addr;
        input [7:0] idx;
        begin
            case (idx)
                8'd0:   seq_reg_addr = 12'h000;
                8'd1:   seq_reg_addr = 12'h000;
                8'd3:   seq_reg_addr = 12'h000;
                8'd4:   seq_reg_addr = 12'h0C8;
                8'd5:   seq_reg_addr = 12'h0D2;
                8'd6:   seq_reg_addr = 12'h0DC;
                8'd7:   seq_reg_addr = 12'h0E6;
                8'd8:   seq_reg_addr = 12'h0F0;
                8'd9:   seq_reg_addr = 12'h0FA;
                8'd10:  seq_reg_addr = 12'h104;
                8'd11:  seq_reg_addr = 12'h10E;
                8'd12:  seq_reg_addr = 12'h118;
                8'd13:  seq_reg_addr = 12'h122;
                8'd14:  seq_reg_addr = 12'h12C;
                8'd15:  seq_reg_addr = 12'h136;
                8'd16:  seq_reg_addr = 12'h140;
                8'd17:  seq_reg_addr = 12'h14A;
                8'd18:  seq_reg_addr = 12'h09F;
                8'd19:  seq_reg_addr = 12'h0A0;
                8'd20:  seq_reg_addr = 12'h0A5;
                8'd21:  seq_reg_addr = 12'h0A8;
                8'd22:  seq_reg_addr = 12'h0B0;
                8'd23:  seq_reg_addr = 12'h005;
                8'd24:  seq_reg_addr = 12'h003;
                8'd25:  seq_reg_addr = 12'h033;
                8'd26:  seq_reg_addr = 12'h034;
                8'd27:  seq_reg_addr = 12'h035;
                8'd28:  seq_reg_addr = 12'h036;
                8'd29:  seq_reg_addr = 12'h032;
                8'd30:  seq_reg_addr = 12'h01A;
                8'd31:  seq_reg_addr = 12'h028;
                8'd32:  seq_reg_addr = 12'h01C;
                8'd33:  seq_reg_addr = 12'h01D;
                8'd34:  seq_reg_addr = 12'h01E;
                8'd35:  seq_reg_addr = 12'h01F;
                8'd36:  seq_reg_addr = 12'h020;
                8'd37:  seq_reg_addr = 12'h021;
                8'd38:  seq_reg_addr = 12'h022;
                8'd39:  seq_reg_addr = 12'h026;
                8'd40:  seq_reg_addr = 12'h027;
                8'd41:  seq_reg_addr = 12'h014;
                8'd42:  seq_reg_addr = 12'h029;
                8'd43:  seq_reg_addr = 12'h05C;
                8'd44:  seq_reg_addr = 12'h05D;
                8'd45:  seq_reg_addr = 12'h05A;
                8'd46:  seq_reg_addr = 12'h00A;
                8'd48:  seq_reg_addr = 12'h00B;
                8'd49:  seq_reg_addr = 12'h00C;
                8'd50:  seq_reg_addr = 12'h00D;
                8'd51:  seq_reg_addr = 12'h00E;
                8'd52:  seq_reg_addr = 12'h046;
                8'd53:  seq_reg_addr = 12'h047;
                8'd54:  seq_reg_addr = 12'h048;
                8'd55:  seq_reg_addr = 12'h049;
                8'd56:  seq_reg_addr = 12'h050;
                8'd57:  seq_reg_addr = 12'h051;
                8'd58:  seq_reg_addr = 12'h052;
                8'd59:  seq_reg_addr = 12'h053;
                8'd60:  seq_reg_addr = 12'h0C9;
                8'd61:  seq_reg_addr = 12'h0CA;
                8'd62:  seq_reg_addr = 12'h0D0;
                8'd63:  seq_reg_addr = 12'h0CB;
                8'd64:  seq_reg_addr = 12'h0CC;
                8'd65:  seq_reg_addr = 12'h0CF;
                8'd66:  seq_reg_addr = 12'h0C8;
                8'd67:  seq_reg_addr = 12'h0D3;
                8'd68:  seq_reg_addr = 12'h0D4;
                8'd69:  seq_reg_addr = 12'h0DA;
                8'd70:  seq_reg_addr = 12'h0D5;
                8'd71:  seq_reg_addr = 12'h0D6;
                8'd72:  seq_reg_addr = 12'h0D9;
                8'd73:  seq_reg_addr = 12'h0D2;
                8'd74:  seq_reg_addr = 12'h0DD;
                8'd75:  seq_reg_addr = 12'h0DE;
                8'd76:  seq_reg_addr = 12'h0E4;
                8'd77:  seq_reg_addr = 12'h0DF;
                8'd78:  seq_reg_addr = 12'h0E0;
                8'd79:  seq_reg_addr = 12'h0E3;
                8'd80:  seq_reg_addr = 12'h0DC;
                8'd81:  seq_reg_addr = 12'h0E7;
                8'd82:  seq_reg_addr = 12'h0E8;
                8'd83:  seq_reg_addr = 12'h0EE;
                8'd84:  seq_reg_addr = 12'h0E9;
                8'd85:  seq_reg_addr = 12'h0EA;
                8'd86:  seq_reg_addr = 12'h0ED;
                8'd87:  seq_reg_addr = 12'h0E6;
                8'd88:  seq_reg_addr = 12'h0F1;
                8'd89:  seq_reg_addr = 12'h0F2;
                8'd90:  seq_reg_addr = 12'h0F8;
                8'd91:  seq_reg_addr = 12'h0F3;
                8'd92:  seq_reg_addr = 12'h0F4;
                8'd93:  seq_reg_addr = 12'h0F7;
                8'd94:  seq_reg_addr = 12'h0F0;
                8'd95:  seq_reg_addr = 12'h0FB;
                8'd96:  seq_reg_addr = 12'h0FC;
                8'd97:  seq_reg_addr = 12'h102;
                8'd98:  seq_reg_addr = 12'h0FD;
                8'd99:  seq_reg_addr = 12'h0FE;
                8'd100: seq_reg_addr = 12'h101;
                8'd101: seq_reg_addr = 12'h0FA;
                8'd102: seq_reg_addr = 12'h119;
                8'd103: seq_reg_addr = 12'h11A;
                8'd104: seq_reg_addr = 12'h120;
                8'd105: seq_reg_addr = 12'h11B;
                8'd106: seq_reg_addr = 12'h11C;
                8'd107: seq_reg_addr = 12'h11F;
                8'd108: seq_reg_addr = 12'h118;
                8'd109: seq_reg_addr = 12'h123;
                8'd110: seq_reg_addr = 12'h124;
                8'd111: seq_reg_addr = 12'h12A;
                8'd112: seq_reg_addr = 12'h125;
                8'd113: seq_reg_addr = 12'h126;
                8'd114: seq_reg_addr = 12'h129;
                8'd115: seq_reg_addr = 12'h122;
                8'd116: seq_reg_addr = 12'h001;
                8'd118: seq_reg_addr = 12'h001;
                8'd119: seq_reg_addr = 12'h001;
                8'd121: seq_reg_addr = 12'h001;
                8'd123: seq_reg_addr = 12'h001;
                8'd125: seq_reg_addr = 12'h001;
                default: seq_reg_addr = 12'h000;
            endcase
        end
    endfunction

    function [7:0] seq_reg_data;
        input [7:0] idx;
        begin
            case (idx)
                8'd0:   seq_reg_data = 8'h01;
                8'd1:   seq_reg_data = 8'h00;
                8'd3:   seq_reg_data = 8'h00;
                8'd4:   seq_reg_data = 8'h00;
                8'd5:   seq_reg_data = 8'h00;
                8'd6:   seq_reg_data = 8'h00;
                8'd7:   seq_reg_data = 8'h00;
                8'd8:   seq_reg_data = 8'h00;
                8'd9:   seq_reg_data = 8'h00;
                8'd10:  seq_reg_data = 8'h00;
                8'd11:  seq_reg_data = 8'h00;
                8'd12:  seq_reg_data = 8'h00;
                8'd13:  seq_reg_data = 8'h00;
                8'd14:  seq_reg_data = 8'h00;
                8'd15:  seq_reg_data = 8'h00;
                8'd16:  seq_reg_data = 8'h00;
                8'd17:  seq_reg_data = 8'h00;
                8'd18:  seq_reg_data = 8'h4D;
                8'd19:  seq_reg_data = 8'hDF;
                8'd20:  seq_reg_data = 8'h06;
                8'd21:  seq_reg_data = 8'h06;
                8'd22:  seq_reg_data = 8'h04;
                8'd23:  seq_reg_data = 8'h01;
                8'd24:  seq_reg_data = 8'h2F;
                8'd25:  seq_reg_data = 8'h01;
                8'd26:  seq_reg_data = 8'h00;
                8'd27:  seq_reg_data = 8'h0F;
                8'd28:  seq_reg_data = 8'h00;
                8'd29:  seq_reg_data = 8'h00;
                8'd30:  seq_reg_data = 8'h08;
                8'd31:  seq_reg_data = 8'h11;
                8'd32:  seq_reg_data = 8'h01;
                8'd33:  seq_reg_data = 8'h01;
                8'd34:  seq_reg_data = 8'h01;
                8'd35:  seq_reg_data = 8'h01;
                8'd36:  seq_reg_data = 8'h0A;
                8'd37:  seq_reg_data = 8'h01;
                8'd38:  seq_reg_data = 8'h00;
                8'd39:  seq_reg_data = 8'h0A;
                8'd40:  seq_reg_data = 8'h00;
                8'd41:  seq_reg_data = 8'h00;
                8'd42:  seq_reg_data = 8'h05;
                8'd43:  seq_reg_data = 8'h00;
                8'd44:  seq_reg_data = 8'h06;
                8'd45:  seq_reg_data = 8'h01;
                8'd46:  seq_reg_data = 8'h21;
                8'd48:  seq_reg_data = 8'h00;
                8'd49:  seq_reg_data = 8'h00;
                8'd50:  seq_reg_data = 8'h00;
                8'd51:  seq_reg_data = 8'h07;
                8'd52:  seq_reg_data = 8'h00;
                8'd53:  seq_reg_data = 8'h00;
                8'd54:  seq_reg_data = 8'h00;
                8'd55:  seq_reg_data = 8'h00;
                8'd56:  seq_reg_data = 8'h1F;
                8'd57:  seq_reg_data = 8'h2B;
                8'd58:  seq_reg_data = 8'h33;
                8'd59:  seq_reg_data = 8'h37;
                8'd60:  seq_reg_data = 8'h18;
                8'd61:  seq_reg_data = 8'h00;
                8'd62:  seq_reg_data = 8'h11;
                8'd63:  seq_reg_data = 8'h00;
                8'd64:  seq_reg_data = 8'h00;
                8'd65:  seq_reg_data = 8'h00;
                8'd66:  seq_reg_data = 8'hD1;
                8'd67:  seq_reg_data = 8'h18;
                8'd68:  seq_reg_data = 8'h00;
                8'd69:  seq_reg_data = 8'h11;
                8'd70:  seq_reg_data = 8'h00;
                8'd71:  seq_reg_data = 8'h00;
                8'd72:  seq_reg_data = 8'h00;
                8'd73:  seq_reg_data = 8'hD1;
                8'd74:  seq_reg_data = 8'h1E;
                8'd75:  seq_reg_data = 8'h00;
                8'd76:  seq_reg_data = 8'h11;
                8'd77:  seq_reg_data = 8'h00;
                8'd78:  seq_reg_data = 8'h00;
                8'd79:  seq_reg_data = 8'h00;
                8'd80:  seq_reg_data = 8'hD1;
                8'd81:  seq_reg_data = 8'h00;
                8'd82:  seq_reg_data = 8'h03;
                8'd83:  seq_reg_data = 8'h10;
                8'd84:  seq_reg_data = 8'h00;
                8'd85:  seq_reg_data = 8'h00;
                8'd86:  seq_reg_data = 8'h00;
                8'd87:  seq_reg_data = 8'hD1;
                8'd88:  seq_reg_data = 8'h03;
                8'd89:  seq_reg_data = 8'h00;
                8'd90:  seq_reg_data = 8'h01;
                8'd91:  seq_reg_data = 8'h00;
                8'd92:  seq_reg_data = 8'h00;
                8'd93:  seq_reg_data = 8'h00;
                8'd94:  seq_reg_data = 8'hD1;
                8'd95:  seq_reg_data = 8'h00;
                8'd96:  seq_reg_data = 8'h03;
                8'd97:  seq_reg_data = 8'h10;
                8'd98:  seq_reg_data = 8'h00;
                8'd99:  seq_reg_data = 8'h00;
                8'd100: seq_reg_data = 8'h00;
                8'd101: seq_reg_data = 8'hD1;
                8'd102: seq_reg_data = 8'h03;
                8'd103: seq_reg_data = 8'h00;
                8'd104: seq_reg_data = 8'h01;
                8'd105: seq_reg_data = 8'h00;
                8'd106: seq_reg_data = 8'h00;
                8'd107: seq_reg_data = 8'h00;
                8'd108: seq_reg_data = 8'hD1;
                8'd109: seq_reg_data = 8'h00;
                8'd110: seq_reg_data = 8'h03;
                8'd111: seq_reg_data = 8'h10;
                8'd112: seq_reg_data = 8'h00;
                8'd113: seq_reg_data = 8'h00;
                8'd114: seq_reg_data = 8'h00;
                8'd115: seq_reg_data = 8'hD1;
                8'd116: seq_reg_data = 8'h02;
                8'd118: seq_reg_data = 8'h00;
                8'd119: seq_reg_data = 8'h80;
                8'd121: seq_reg_data = 8'h00;
                8'd123: seq_reg_data = 8'h04;
                8'd125: seq_reg_data = 8'h00;
                default: seq_reg_data = 8'h00;
            endcase
        end
    endfunction

    always @(posedge clk) begin
        if (rst) begin
            state <= ST_IDLE;
            busy <= 1'b0;
            done <= 1'b0;
            reset_out <= 1'b1;
            spi_cs_n <= 1'b1;
            spi_sclk <= 1'b0;
            spi_sdio_o <= 1'b0;
            spi_sdio_oe <= 1'b0;
            step_index <= 8'd0;
            last_addr <= 12'd0;
            last_data <= 8'd0;
            readback_done <= 1'b0;
            readback_sdio_stuck <= 1'b0;
            readback_index <= 4'd0;
            readback_last_addr <= 12'd0;
            readback_last_data <= 8'd0;
            readback_id_word <= 32'd0;
            readback_alarm_word <= 32'd0;
            readback_pll1_word <= 32'd0;
            readback_pll2_word <= 32'd0;
            delay_count <= 32'd0;
            spi_div_count <= 32'd0;
            spi_shift <= 24'd0;
            spi_bits_left <= 6'd0;
            spi_half_phase <= 1'b0;
            spi_read_mode <= 1'b0;
            spi_read_shift <= 8'd0;
        end else begin
            case (state)
                ST_IDLE: begin
                    busy <= 1'b1;
                    done <= 1'b0;
                    readback_done <= 1'b0;
                    readback_sdio_stuck <= 1'b0;
                    readback_index <= 4'd0;
                    readback_last_addr <= 12'd0;
                    readback_last_data <= 8'd0;
                    readback_id_word <= 32'd0;
                    readback_alarm_word <= 32'd0;
                    readback_pll1_word <= 32'd0;
                    readback_pll2_word <= 32'd0;
                    reset_out <= 1'b1;
                    spi_cs_n <= 1'b1;
                    spi_sclk <= 1'b0;
                    spi_sdio_oe <= 1'b0;
                    step_index <= 8'd0;
                    delay_count <= RESET_ASSERT_CYCLES[31:0];
                    state <= ST_RESET_ASSERT;
                end

                ST_RESET_ASSERT: begin
                    if (delay_count == 32'd0) begin
                        reset_out <= 1'b0;
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
                    spi_sdio_oe <= 1'b0;
                    spi_read_mode <= 1'b0;
                    if (step_index >= SEQ_LEN) begin
                        readback_index <= 4'd0;
                        state <= ST_READ_LOAD;
                    end else if (seq_delay) begin
                        delay_count <= seq_delay_cycles;
                        step_index <= step_index + 1'b1;
                        state <= ST_DELAY;
                    end else begin
                        last_addr <= seq_addr;
                        last_data <= seq_data;
                        spi_shift <= {4'b0000, seq_addr, seq_data};
                        spi_bits_left <= 6'd24;
                        spi_half_phase <= 1'b0;
                        spi_div_count <= SPI_HALF_CYCLES[31:0] - 1'b1;
                        spi_cs_n <= 1'b0;
                        spi_sdio_oe <= 1'b1;
                        spi_sdio_o <= 1'b0; // First write-command bit; stable before first SCLK edge.
                        state <= ST_SPI;
                    end
                end

                ST_READ_LOAD: begin
                    spi_sclk <= 1'b0;
                    spi_sdio_oe <= 1'b0;
                    if (readback_index >= READ_LEN) begin
                        readback_done <= 1'b1;
                        readback_sdio_stuck <= (readback_id_word[23:0] == 24'h000000) ||
                                               (readback_id_word[23:0] == 24'hffffff);
                        state <= ST_DONE;
                    end else begin
                        readback_last_addr <= read_addr;
                        spi_shift <= {1'b1, 3'b000, read_addr, 8'h00};
                        spi_read_shift <= 8'd0;
                        spi_bits_left <= 6'd24;
                        spi_half_phase <= 1'b0;
                        spi_read_mode <= 1'b1;
                        spi_div_count <= SPI_HALF_CYCLES[31:0] - 1'b1;
                        spi_cs_n <= 1'b0;
                        spi_sdio_oe <= 1'b1;
                        spi_sdio_o <= 1'b1; // First read-command bit; stable before first SCLK edge.
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
                            if (spi_read_mode && (spi_bits_left <= 6'd8)) begin
                                spi_read_shift <= {spi_read_shift[6:0], spi_sdio_i};
                            end
                        end else begin
                            spi_sclk <= 1'b0;
                            spi_half_phase <= 1'b0;
                            if (spi_bits_left == 6'd1) begin
                                spi_cs_n <= 1'b1;
                                spi_sdio_oe <= 1'b0;
                                if (spi_read_mode) begin
                                    readback_last_data <= spi_read_shift;
                                    case (readback_index)
                                        4'd0:  readback_id_word[7:0] <= spi_read_shift;
                                        4'd1:  readback_id_word[15:8] <= spi_read_shift;
                                        4'd2:  readback_id_word[23:16] <= spi_read_shift;
                                        4'd3:  readback_alarm_word[7:0] <= spi_read_shift;
                                        4'd4:  readback_alarm_word[15:8] <= spi_read_shift;
                                        4'd5:  readback_alarm_word[23:16] <= spi_read_shift;
                                        4'd6:  readback_alarm_word[31:24] <= spi_read_shift;
                                        4'd7:  readback_pll1_word[7:0] <= spi_read_shift;
                                        4'd8:  readback_pll1_word[15:8] <= spi_read_shift;
                                        4'd9:  readback_pll1_word[23:16] <= spi_read_shift;
                                        4'd10: readback_pll1_word[31:24] <= spi_read_shift;
                                        4'd11: readback_pll2_word[7:0] <= spi_read_shift;
                                        4'd12: readback_pll2_word[15:8] <= spi_read_shift;
                                        default: begin end
                                    endcase
                                    readback_index <= readback_index + 1'b1;
                                    state <= ST_READ_LOAD;
                                end else begin
                                    step_index <= step_index + 1'b1;
                                    state <= ST_LOAD;
                                end
                            end else if (spi_read_mode && (spi_bits_left == 6'd9)) begin
                                spi_sdio_oe <= 1'b0;
                                spi_bits_left <= spi_bits_left - 1'b1;
                            end else begin
                                if (!spi_read_mode || (spi_bits_left > 6'd9)) begin
                                    spi_sdio_o <= spi_shift[22];
                                end
                                spi_shift <= {spi_shift[22:0], 1'b0};
                                spi_bits_left <= spi_bits_left - 1'b1;
                            end
                        end
                    end
                end

                ST_DELAY: begin
                    if (delay_count == 32'd0) begin
                        state <= ST_LOAD;
                    end else begin
                        delay_count <= delay_count - 1'b1;
                    end
                end

                ST_DONE: begin
                    busy <= 1'b0;
                    done <= 1'b1;
                    readback_done <= 1'b1;
                    reset_out <= 1'b0;
                    spi_cs_n <= 1'b1;
                    spi_sclk <= 1'b0;
                    spi_sdio_oe <= 1'b0;
                end

                default: begin
                    state <= ST_IDLE;
                end
            endcase
        end
    end

endmodule

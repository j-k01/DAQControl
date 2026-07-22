`timescale 1ns/1ps

// Four-bank integration test: AXI writes -> independent pulse RAMs -> shapers
// -> per-neuron calibration. Distinct data and lengths prove that calibration
// extends the pulse path without modifying neuron/current-source logic.
module spike_shape_per_neuron_tb;
    localparam integer AW = 3;
    localparam integer OBS = 24;

    reg axi_clk = 1'b0;
    reg dac_clk = 1'b0;
    reg reset = 1'b1;
    always #5 axi_clk = ~axi_clk;
    always #2 dac_clk = ~dac_clk;

    reg [31:0] axi_addr = 32'd0;
    reg [31:0] axi_din = 32'd0;
    wire [31:0] axi_dout;
    reg axi_en = 1'b0;
    reg [3:0] axi_we = 4'd0;
    reg [3:0] spike = 4'd0;
    reg [AW:0] nbeats0 = 1, nbeats1 = 2, nbeats2 = 3, nbeats3 = 1;
    reg [31:0] cal0 = 32'h0000_4000;
    reg [31:0] cal1 = 32'h0064_2000; // +100, +0.5x
    reg [31:0] cal2 = 32'h0000_C000; // 0, -1.0x
    reg [31:0] cal3 = 32'h007B_4000; // +123, +1.0x

    wire [AW-1:0] addr0, addr1, addr2, addr3;
    wire [63:0] data0, data1, data2, data3;
    wire [63:0] raw0, raw1, raw2, raw3;
    wire [63:0] out0, out1, out2, out3;
    reg [63:0] obs0 [0:OBS-1];
    reg [63:0] obs1 [0:OBS-1];
    reg [63:0] obs2 [0:OBS-1];
    reg [63:0] obs3 [0:OBS-1];
    integer errors = 0;

    spike_shape_bram_bank #(.ADDR_W(AW)) u_bank (
        .axi_addr(axi_addr), .axi_clk(axi_clk), .axi_din(axi_din),
        .axi_dout(axi_dout), .axi_en(axi_en), .axi_we(axi_we),
        .fabric_clk(dac_clk),
        .fabric_addr0(addr0), .fabric_dout0(data0),
        .fabric_addr1(addr1), .fabric_dout1(data1),
        .fabric_addr2(addr2), .fabric_dout2(data2),
        .fabric_addr3(addr3), .fabric_dout3(data3));

    izh_spike_shaper #(.ADDR_W(AW)) u_s0 (
        .clk(dac_clk), .reset(reset), .spike(spike[0]), .shape_addr(addr0),
        .shape_data(data0), .nbeats(nbeats0), .active(), .dac_word(raw0));
    izh_spike_shaper #(.ADDR_W(AW)) u_s1 (
        .clk(dac_clk), .reset(reset), .spike(spike[1]), .shape_addr(addr1),
        .shape_data(data1), .nbeats(nbeats1), .active(), .dac_word(raw1));
    izh_spike_shaper #(.ADDR_W(AW)) u_s2 (
        .clk(dac_clk), .reset(reset), .spike(spike[2]), .shape_addr(addr2),
        .shape_data(data2), .nbeats(nbeats2), .active(), .dac_word(raw2));
    izh_spike_shaper #(.ADDR_W(AW)) u_s3 (
        .clk(dac_clk), .reset(reset), .spike(spike[3]), .shape_addr(addr3),
        .shape_data(data3), .nbeats(nbeats3), .active(), .dac_word(raw3));

    spike_calibrate u_c0 (.clk(dac_clk), .rst(reset), .cal(cal0),
                          .in_word(raw0), .out_word(out0));
    spike_calibrate u_c1 (.clk(dac_clk), .rst(reset), .cal(cal1),
                          .in_word(raw1), .out_word(out1));
    spike_calibrate u_c2 (.clk(dac_clk), .rst(reset), .cal(cal2),
                          .in_word(raw2), .out_word(out2));
    spike_calibrate u_c3 (.clk(dac_clk), .rst(reset), .cal(cal3),
                          .in_word(raw3), .out_word(out3));

    task write_word(input integer bank, input integer word_addr,
                    input [31:0] value);
        begin
            @(negedge axi_clk);
            axi_addr = (bank << (AW + 3)) + (word_addr << 2);
            axi_din = value;
            axi_en = 1'b1;
            axi_we = 4'hF;
            @(negedge axi_clk);
            axi_en = 1'b0;
            axi_we = 4'd0;
        end
    endtask

    task check_sequence;
        integer i;
        integer s0, s1, s2, s3;
        begin
            s0 = -1; s1 = -1; s2 = -1; s3 = -1;
            for (i = 0; i < OBS; i = i + 1) begin
                if (s0 < 0 && obs0[i] === 64'h0FA0_0BB8_07D0_03E8) s0 = i;
                if (s1 < 0 && obs1[i] === 64'h01F4_0190_012C_00C8) s1 = i;
                if (s2 < 0 && obs2[i] === 64'hFF38_FF6A_FF9C_FFCE) s2 = i;
                if (s3 < 0 && obs3[i] === 64'h007F_007E_007D_007C) s3 = i;
            end
            if (s0 < 0 || s1 < 0 || s2 < 0 || s3 < 0) begin
                errors = errors + 1;
                $display("FAIL first beats s0=%0d s1=%0d s2=%0d s3=%0d", s0, s1, s2, s3);
            end else begin
                if (s0 != s1 || s0 != s2 || s0 != s3) begin
                    errors = errors + 1;
                    $display("FAIL calibrated shaper alignment %0d/%0d/%0d/%0d", s0, s1, s2, s3);
                end
                if (obs0[s0+1] !== 64'd0) errors = errors + 1;
                if (obs1[s1+1] !== 64'hFED4_FF38_FF9C_0000 ||
                    obs1[s1+2] !== 64'h0064_0064_0064_0064) errors = errors + 1;
                if (obs2[s2+1] !== 64'hFE70_FEA2_FED4_FF06 ||
                    obs2[s2+2] !== 64'h00C8_0096_0064_0032 ||
                    obs2[s2+3] !== 64'd0) errors = errors + 1;
                if (obs3[s3+1] !== 64'h007B_007B_007B_007B) errors = errors + 1;
            end
        end
    endtask

    integer i;
    initial begin
        // Bank 0: one beat {4000,3000,2000,1000}.
        write_word(0, 0, 32'h07D0_03E8);
        write_word(0, 1, 32'h0FA0_0BB8);
        // Bank 1: two beats, then +0.5x and +100 calibration.
        write_word(1, 0, 32'h0190_00C8);
        write_word(1, 1, 32'h0320_0258);
        write_word(1, 2, 32'hFE70_FF38);
        write_word(1, 3, 32'hFCE0_FDA8);
        // Bank 2: three beats, then inverted by its own calibration.
        write_word(2, 0, 32'h0064_0032);
        write_word(2, 1, 32'h00C8_0096);
        write_word(2, 2, 32'h012C_00FA);
        write_word(2, 3, 32'h0190_015E);
        write_word(2, 4, 32'hFF9C_FFCE);
        write_word(2, 5, 32'hFF38_FF6A);
        // Bank 3: one small beat on a continuous +123 baseline.
        write_word(3, 0, 32'h0002_0001);
        write_word(3, 1, 32'h0004_0003);

        repeat (6) @(negedge dac_clk);
        reset = 1'b0;
        repeat (5) @(negedge dac_clk);
        spike = 4'hF;
        @(negedge dac_clk);
        spike = 4'd0;
        for (i = 0; i < OBS; i = i + 1) begin
            @(negedge dac_clk);
            obs0[i] = out0; obs1[i] = out1;
            obs2[i] = out2; obs3[i] = out3;
        end
        check_sequence();

        if (errors == 0)
            $display("TB_RESULT: PASS spike_shape_per_neuron");
        else
            $display("TB_RESULT: FAIL spike_shape_per_neuron errors=%0d", errors);
        $finish;
    end

    initial begin
        #100000;
        $display("TB_RESULT: FAIL spike_shape_per_neuron TIMEOUT");
        $finish;
    end
endmodule

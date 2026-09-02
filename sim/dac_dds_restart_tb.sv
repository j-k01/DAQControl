module dac_dds_restart_tb;
    reg clk = 1'b0;
    reg rst = 1'b1;
    reg restart = 1'b0;
    wire [31:0] sine_word;
    wire tone_start;
    wire [255:0] source_words;

    localparam [63:0] PHASE_ZERO_BEAT = {
        16'd30502, 16'd23457, 16'd12728, 16'd0
    };

    always #2 clk = ~clk;

    daq_litejesd_dac_tx_path dut (
        .jesd_clk(clk),
        .jesd_rst(rst),
        .phy_tx_clk(clk),
        .phy_tx_rst(8'h00),
        .enable(1'b1),
        .stpl_enable(1'b0),
        .sysref(1'b0),
        .sync_n(1'b1),
        .active_converter(3'd0),
        .sample_map_mode(2'd0),
        .physical_map_mode(4'd0),
        .triangle_step(16'd1),
        .sine_phase_inc(24'h100000),
        .tone_restart(restart),
        .dac_src_sel(16'h1111),
        .mon_words(256'd0),
        .current_word(64'd0),
        .tag_source_enable(1'b0),
        .program_enable(1'b0),
        .program_word0(64'd0),
        .program_word1(64'd0),
        .program_word2(64'd0),
        .program_word3(64'd0),
        .neuron_word0(64'd0),
        .neuron_word1(64'd0),
        .neuron_word2(64'd0),
        .neuron_word3(64'd0),
        .sine_word(sine_word),
        .tone_start(tone_start),
        .debug_source_words(source_words)
    );

    task automatic fail(input string message);
        begin
            $display("TB_RESULT: FAIL: %s", message);
            $fatal(1);
        end
    endtask

    task automatic restart_and_check;
        begin
            @(negedge clk);
            restart = 1'b1;
            @(posedge clk);
            #1;
            if (tone_start !== 1'b1)
                fail("tone_start did not mark restart");
            if (sine_word !== {16'd12728, 16'd0})
                fail("restart did not begin at phase zero");

            @(negedge clk);
            restart = 1'b0;
            @(posedge clk);
            #1;
            if (tone_start !== 1'b0)
                fail("tone_start was not one cycle");
            if (source_words[63:0] !== PHASE_ZERO_BEAT)
                fail("DAC0 phase-zero beat mismatch");
            if (source_words[127:64] !== PHASE_ZERO_BEAT)
                fail("DAC1 DDS route mismatch");
            if (source_words[191:128] !== PHASE_ZERO_BEAT)
                fail("DAC2 DDS route mismatch");
            if (source_words[255:192] !== PHASE_ZERO_BEAT)
                fail("DAC3 DDS route mismatch");
        end
    endtask

    initial begin
        repeat (4) @(posedge clk);
        rst = 1'b0;
        repeat (7) @(posedge clk);
        restart_and_check();
        repeat (13) @(posedge clk);
        restart_and_check();
        repeat (3) @(posedge clk);
        $display("TB_RESULT: PASS");
        $finish;
    end
endmodule
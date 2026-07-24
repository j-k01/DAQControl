`timescale 1ns/1ps

// Proves the injected-current path reaches all real neuron cores and documents
// the timing trap seen by the GUI: the power-on 256-clock update period cannot
// react to a 48-sample (960 ns) pulse, while explicit period=1/dt=0.5 timing
// produces spikes under the same positive current drive.
module izh_current_to_spike_tb;
    reg clk = 0;
    always #10 clk = ~clk; // 50 MHz

    reg reset = 1;
    reg prog_start = 0;
    reg experiment_restart = 0;
    reg signed [31:0] i_external = 0;
    wire [5:0] cfg_addr;
    reg [31:0] cfg_data = 0;
    reg [31:0] cfg_mem [0:63];
    wire [3:0] spike_flags;
    wire [127:0] i_mon;
    wire [31:0] debug_word;

    izh_dac_bank dut (
        .clk(clk),
        .reset(reset),
        .prog_start(prog_start),
        .experiment_restart(experiment_restart),
        .i_external(i_external),
        .cfg_addr(cfg_addr),
        .cfg_data(cfg_data),
        .spike_flags(spike_flags),
        .i_mon(i_mon),
        .debug_word(debug_word)
    );

    always @(posedge clk)
        cfg_data <= cfg_mem[cfg_addr];

    integer i;
    integer short_spikes;
    integer fast_spikes [0:3];
    initial begin
        for (i = 0; i < 64; i = i + 1)
            cfg_mem[i] = 0;
        // Global-only load: dt=0.5 and one neuron update per clk_50 cycle.
        cfg_mem[0] = 32'h0000_0100;
        cfg_mem[1] = 32'h0000_8000;
        cfg_mem[2] = 32'd1;

        repeat (5) @(posedge clk);
        reset <= 0;
        @(posedge clk);

        short_spikes = 0;
        i_external <= 32'sh000F_0000; // +15, total current = default Ic 10 + 15
        repeat (48) begin
            @(posedge clk);
            if (|spike_flags)
                short_spikes = short_spikes + 1;
        end
        i_external <= 0;
        repeat (20) @(posedge clk);
        if (short_spikes != 0) begin
            $display("TB_RESULT: FAIL power-on timing unexpectedly reacted to 960 ns pulse");
            $finish;
        end

        prog_start <= 1;
        @(posedge clk);
        prog_start <= 0;
        repeat (90) @(posedge clk);
        experiment_restart <= 1;
        @(posedge clk);
        experiment_restart <= 0;
        i_external <= 32'sh000F_0000;
        for (i = 0; i < 4; i = i + 1)
            fast_spikes[i] = 0;
        repeat (4000) begin
            @(posedge clk);
            for (i = 0; i < 4; i = i + 1)
                if (spike_flags[i])
                    fast_spikes[i] = fast_spikes[i] + 1;
        end
        for (i = 0; i < 4; i = i + 1) begin
            if (fast_spikes[i] == 0) begin
                $display("TB_RESULT: FAIL neuron %0d did not spike from i_external", i);
                $finish;
            end
        end

        $display(
            "TB_RESULT: PASS current reaches all neurons; short/default timing misses, explicit fast timing spikes (%0d,%0d,%0d,%0d)",
            fast_spikes[0], fast_spikes[1], fast_spikes[2], fast_spikes[3]
        );
        $finish;
    end

    initial begin
        #200000;
        $display("TB_RESULT: FAIL izh_current_to_spike TIMEOUT");
        $finish;
    end
endmodule

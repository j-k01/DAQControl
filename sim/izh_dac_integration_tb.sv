`timescale 1ns/1ps

// Self-checking TB for the current izh_dac_bank interface.  The bank is
// programmed through the same 1-cycle-latency config-BRAM port used in top.v.
// Verifies default current monitor values, injected-current summing, per-neuron
// I/I_const programming, and global dt/period loading.

module izh_dac_integration_tb;
    reg clk = 1'b0;
    always #5 clk = ~clk;

    reg reset = 1'b1;
    reg prog_start = 1'b0;
    reg signed [31:0] i_external = 32'sd0;

    wire [5:0]  cfg_addr;
    reg  [31:0] cfg_data = 32'd0;
    wire [3:0]  spike_flags;
    wire [127:0] i_mon;
    wire [31:0] debug_word;

    reg [31:0] cfg_mem [0:63];
    always @(posedge clk)
        cfg_data <= cfg_mem[cfg_addr];

    izh_dac_bank #(.ADDR_W(6)) u_bank (
        .clk         (clk),
        .reset       (reset),
        .prog_start  (prog_start),
        .i_external  (i_external),
        .cfg_addr    (cfg_addr),
        .cfg_data    (cfg_data),
        .spike_flags (spike_flags),
        .i_mon       (i_mon),
        .debug_word  (debug_word)
    );

    task tick;
        begin
            @(posedge clk);
            #1;
        end
    endtask

    task check32;
        input [255:0] label;
        input [31:0] actual;
        input [31:0] expected;
        begin
            if (actual !== expected) begin
                $display("FAIL %0s actual=0x%08x expected=0x%08x",
                         label, actual, expected);
                $fatal;
            end
        end
    endtask

    task clear_cfg;
        integer i;
        begin
            for (i = 0; i < 64; i = i + 1)
                cfg_mem[i] = 32'd0;
        end
    endtask

    task pulse_prog;
        begin
            @(negedge clk); prog_start = 1'b1;
            @(negedge clk); prog_start = 1'b0;
            repeat (90) tick();
        end
    endtask

    function [31:0] imon_word;
        input integer n;
        begin
            case (n)
            0: imon_word = i_mon[0*32 +: 32];
            1: imon_word = i_mon[1*32 +: 32];
            2: imon_word = i_mon[2*32 +: 32];
            3: imon_word = i_mon[3*32 +: 32];
            default: imon_word = 32'hDEAD_BEEF;
            endcase
        end
    endfunction

    initial begin
        clear_cfg();

        repeat (5) tick();
        reset = 1'b0;
        repeat (3) tick();

        // Defaults: I=0, I_const=10, so i_mon=10.0.
        check32("default i_mon ch0", imon_word(0), 32'h000A_0000);
        check32("default i_mon ch3", imon_word(3), 32'h000A_0000);

        // Injected current is summed into every neuron monitor.
        i_external = 32'h0005_0000;
        repeat (2) tick();
        check32("external current ch0", imon_word(0), 32'h000F_0000);
        check32("external current ch2", imon_word(2), 32'h000F_0000);

        // Program only neuron 1: I=3.0, I_const=7.0.  With i_external=5.0,
        // ch1 remains 15.0, but via programmed terms instead of defaults.
        clear_cfg();
        cfg_mem[0] = 32'h0000_0002;          // program mask: neuron 1
        cfg_mem[4 + 1*8 + 4] = 32'h0007_0000; // I_const
        cfg_mem[4 + 1*8 + 5] = 32'h0003_0000; // I
        pulse_prog();
        check32("programmed ch1 i_mon", imon_word(1), 32'h000F_0000);
        check32("unprogrammed ch0 i_mon", imon_word(0), 32'h000F_0000);
        check32("ch1 I", u_bank.i_param[1], 32'h0003_0000);
        check32("ch1 I_const", u_bank.i_const[1], 32'h0007_0000);

        // Program global dt/period without touching neuron params.
        clear_cfg();
        cfg_mem[0] = 32'h0000_0100;          // global-set only
        cfg_mem[1] = 32'h0000_2000;          // dt
        cfg_mem[2] = 32'd17;                 // update period
        pulse_prog();
        check32("global dt", u_bank.g_dt, 32'h0000_2000);
        check32("global period", {8'd0, u_bank.g_period}, 32'h0000_0011);
        check32("ch1 I survives global load", u_bank.i_param[1], 32'h0003_0000);

        if (debug_word[31:24] !== 8'h1A) begin
            $display("FAIL debug marker actual=0x%08x", debug_word);
            $fatal;
        end

        $display("TB_RESULT: PASS izh_dac_integration (config bank + current monitor)");
        $finish;
    end
endmodule

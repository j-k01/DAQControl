`timescale 1ns/1ps

// Self-checking TB for izh_spike_shaper with a synchronous BRAM-style shape
// port.  Verifies signed/full-range samples, programmable beat count, idle
// behavior, and playback past the old 16-beat flat-bus wrap point.

module izh_spike_shaper_tb;
    localparam integer AW = 5;                 // 32 beats = 128 samples in TB
    localparam integer NB = (1 << AW);

    reg                  clk = 0, reset = 1, spike = 0;
    wire [AW-1:0]        shape_addr;
    reg  [63:0]          shape_mem [0:NB-1];
    reg  [63:0]          shape_data = 64'd0;
    reg  [AW:0]          nbeats = 6'd2;
    wire                 active;
    wire [63:0]          dac_word;
    integer              errors = 0;

    izh_spike_shaper #(.ADDR_W(AW)) dut (
        .clk(clk), .reset(reset), .spike(spike),
        .shape_addr(shape_addr), .shape_data(shape_data),
        .nbeats(nbeats), .active(active), .dac_word(dac_word)
    );

    always #2 clk = ~clk;

    always @(posedge clk) begin
        shape_data <= shape_mem[shape_addr];
    end

    reg [63:0] played [0:63];
    integer k, b, o, match, okm;

    task run_pulse(input integer expect_nb);
        begin
            @(negedge clk); spike = 1'b1;
            for (k = 0; k < 48; k = k + 1) begin
                @(negedge clk);
                if (k == 0) spike = 1'b0;
                played[k] = dac_word;
            end
            match = -1;
            for (o = 0; (o <= 44) && (match < 0); o = o + 1) begin
                okm = 1;
                for (b = 0; b < expect_nb; b = b + 1)
                    if (played[o+b] !== shape_mem[b]) okm = 0;
                if (okm) match = o;
            end
            if (match < 0) begin
                errors = errors + 1;
                $display("FAIL nb=%0d: pulse not found; first played %h %h %h %h",
                    expect_nb, played[0], played[1], played[2], played[3]);
            end else begin
                if (played[match+expect_nb] !== 64'd0) begin
                    errors = errors + 1;
                    $display("FAIL nb=%0d: no idle after pulse (%h)", expect_nb,
                             played[match+expect_nb]);
                end
                $display("  nb=%0d: OK (offset %0d)", expect_nb, match);
            end
        end
    endtask

    task put_samples4(
        input integer beat,
        input [15:0] s0,
        input [15:0] s1,
        input [15:0] s2,
        input [15:0] s3
    );
        begin
            shape_mem[beat] = {s3, s2, s1, s0};
        end
    endtask

    task run_restart_check;
        integer timeout;
        integer restart0;
        begin
            restart0 = -1;
            nbeats = 6'd6; @(negedge clk);
            @(negedge clk); spike = 1'b1;
            @(negedge clk); spike = 1'b0;

            timeout = 0;
            while ((dac_word !== shape_mem[0]) && (timeout < 16)) begin
                @(negedge clk);
                timeout = timeout + 1;
            end

            if (timeout >= 16) begin
                errors = errors + 1;
                $display("FAIL restart: first pulse beat 0 not found");
            end else begin
                @(negedge clk);
                if (dac_word !== shape_mem[1]) begin
                    errors = errors + 1;
                    $display("FAIL restart: first pulse did not advance to beat1");
                end

                @(negedge clk);                 // still inside the first pulse
                spike = 1'b1;
                @(negedge clk);
                spike = 1'b0;

                for (k = 0; k < 16; k = k + 1) begin
                    @(negedge clk);
                    played[k] = dac_word;
                    if ((restart0 < 0) && (dac_word === shape_mem[0]))
                        restart0 = k;
                end
                if (restart0 < 0) begin
                    errors = errors + 1;
                    $display("FAIL restart: beat 0 not found after restart");
                end else if (played[restart0 + 1] !== shape_mem[1]) begin
                    errors = errors + 1;
                    $display("FAIL restart: after restarted beat0 got next=%h expected=%h",
                             played[restart0 + 1], shape_mem[1]);
                end else begin
                    $display("  restart: OK (restarted beat0 at offset %0d)",
                             restart0);
                end
            end
        end
    endtask
    integer i;
    initial begin
        for (i = 0; i < NB; i = i + 1) shape_mem[i] = 64'd0;
        repeat (3) @(negedge clk);
        reset = 0;

        // 1) trapezoid default: 0 -> +0x6000 plateau -> 0, 7 samples (2 beats)
        put_samples4(0, 16'h1800, 16'h3000, 16'h6000, 16'h6000);
        put_samples4(1, 16'h6000, 16'h3000, 16'h1800, 16'h0000);
        nbeats = 6'd2; @(negedge clk);
        run_pulse(2);

        // 2) biphasic signed, nbeats=2
        put_samples4(0, 16'sd8000, 16'sd8000, 16'sd8000, 16'sd8000);
        put_samples4(1, -16'sd8000, -16'sd8000, -16'sd8000, -16'sd8000);
        nbeats = 6'd2; @(negedge clk);
        run_pulse(2);

        // 3) full-range rails, nbeats=1
        put_samples4(0, 16'sh7FFF, 16'sh8000, 16'sh7FFF, 16'sh8000);
        nbeats = 6'd1; @(negedge clk);
        run_pulse(1);

        // 4) 32 beat-words: proves the BRAM shaper does not wrap at 16 beats.
        for (i = 0; i < NB; i = i + 1)
            shape_mem[i] = {16'(i*4 + 3), 16'(i*4 + 2), 16'(i*4 + 1), 16'(i*4)};
        nbeats = 6'd32; @(negedge clk);
        run_pulse(32);

        // 5) A new spike while active restarts from beat 0.
        for (i = 0; i < 6; i = i + 1)
            shape_mem[i] = {16'(16'hA000 + i), 16'(16'h9000 + i),
                            16'(16'h8000 + i), 16'(16'h7000 + i)};
        run_restart_check();

        if (errors == 0) $display("TB_RESULT: PASS izh_spike_shaper (all checks)");
        else             $display("TB_RESULT: FAIL izh_spike_shaper errors=%0d", errors);
        $finish;
    end

    initial begin #100000; $display("TB_RESULT: FAIL izh_spike_shaper TIMEOUT"); $finish; end
endmodule

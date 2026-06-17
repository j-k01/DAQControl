`timescale 1ns/1ps

// Self-checking TB for izh_spike_shaper (beat-word form): verifies signed/
// full-range samples, programmable beat count, per-beat playback, and idle.

module izh_spike_shaper_tb;
    localparam integer NB = 8;
    reg               clk = 0, reset = 1, spike = 0;
    reg  [15:0]       sarr [0:NB*4-1];     // 64 samples (4 per beat-word)
    wire [NB*64-1:0]  shape;
    reg  [4:0]        nbeats = 5'd2;
    wire              active;
    wire [63:0]       dac_word;
    integer           errors = 0;

    genvar gp;
    generate
        for (gp = 0; gp < NB*4; gp = gp + 1) begin : g_pack
            assign shape[gp*16 +: 16] = sarr[gp];
        end
    endgenerate

    izh_spike_shaper #(.NBEATS_MAX(NB)) dut (
        .clk(clk), .reset(reset), .spike(spike),
        .shape(shape), .nbeats(nbeats), .active(active), .dac_word(dac_word)
    );

    always #2 clk = ~clk;

    reg [63:0] played [0:31];
    reg [63:0] expbw;
    integer k, b, o, match, okm;

    task run_pulse(input integer expect_nb);
        begin
            @(negedge clk); spike = 1'b1;
            for (k = 0; k < 22; k = k + 1) begin
                @(negedge clk);
                if (k == 0) spike = 1'b0;
                played[k] = dac_word;
            end
            match = -1;
            for (o = 0; (o <= 18) && (match < 0); o = o + 1) begin
                okm = 1;
                for (b = 0; b < expect_nb; b = b + 1) begin
                    expbw = {sarr[4*b+3], sarr[4*b+2], sarr[4*b+1], sarr[4*b]};
                    if (played[o+b] !== expbw) okm = 0;
                end
                if (okm) match = o;
            end
            if (match < 0) begin
                errors = errors + 1;
                $display("FAIL nb=%0d: pulse not found; played %h %h %h %h",
                    expect_nb, played[0], played[1], played[2], played[3]);
            end else begin
                if (played[match+expect_nb] !== 64'd0) begin
                    errors = errors + 1;
                    $display("FAIL nb=%0d: no idle after pulse (%h)", expect_nb, played[match+expect_nb]);
                end
                $display("  nb=%0d: OK (offset %0d)", expect_nb, match);
            end
        end
    endtask

    integer i;
    initial begin
        for (i = 0; i < NB*4; i = i + 1) sarr[i] = 16'd0;
        repeat (3) @(negedge clk);
        reset = 0;

        // 1) NEGATIVE trapezoid default: 0 -> -32768 plateau -> 0, 7 samples (nbeats=2)
        sarr[0]=16'hE000; sarr[1]=16'hC000; sarr[2]=16'h8000; sarr[3]=16'h8000;
        sarr[4]=16'h8000; sarr[5]=16'hC000; sarr[6]=16'hE000; sarr[7]=16'h0000;
        nbeats = 5'd2; @(negedge clk);
        run_pulse(2);

        // 2) biphasic signed, nbeats=2
        for (i=0;i<NB*4;i=i+1) sarr[i]=16'd0;
        for (i=0;i<4;i=i+1) sarr[i]=16'sd8000;
        for (i=4;i<8;i=i+1) sarr[i]=-16'sd8000;
        nbeats = 5'd2; @(negedge clk);
        run_pulse(2);

        // 3) full-range rails, nbeats=1
        for (i=0;i<NB*4;i=i+1) sarr[i]=16'd0;
        sarr[0]=16'sh7FFF; sarr[1]=16'sh8000; sarr[2]=16'sh7FFF; sarr[3]=16'sh8000;
        nbeats = 5'd1; @(negedge clk);
        run_pulse(1);

        // 4) full 8 beat-words (32-sample ramp)
        for (i=0;i<NB*4;i=i+1) sarr[i]=$signed(-32768 + i*2048);
        nbeats = 5'd8; @(negedge clk);
        run_pulse(8);

        if (errors == 0) $display("TB_RESULT: PASS izh_spike_shaper (all checks)");
        else             $display("TB_RESULT: FAIL izh_spike_shaper errors=%0d", errors);
        $finish;
    end

    initial begin #50000; $display("TB_RESULT: FAIL izh_spike_shaper TIMEOUT"); $finish; end
endmodule

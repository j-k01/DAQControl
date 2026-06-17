`timescale 1ns/1ps

// Self-checking TB for the 16:4 DAC source crossbar.  Verifies: independent
// per-DAC routing across all 16 select codes, distinct-source mixes, the single
// broadcast DDS entry reaching multiple DACs at once, source 15 routing, and
// off code 0.
//
// The loop variable is a [15:0] vector (not an integer): bit-selecting an
// `integer` crashes xsim 2023.1 elaboration, as do string task args.

module dac_source_crossbar_tb;
    reg  [16*64-1:0] sources;
    reg  [15:0]      sel;
    wire [63:0]      d0, d1, d2, d3;
    reg  [15:0]      idx;
    integer          errors;
    reg  [63:0]      ref_word [0:15];   // expected output for each source code

    dac_source_crossbar dut (
        .sources   (sources),
        .sel       (sel),
        .dac0_word (d0),
        .dac1_word (d1),
        .dac2_word (d2),
        .dac3_word (d3)
    );

    task check(input [63:0] got, input [63:0] exp, input integer id);
        if (got !== exp) begin
            errors = errors + 1;
            $display("FAIL: id=%0d sel=%h got=%h exp=%h", id, sel, got, exp);
        end
    endtask

    initial begin
        errors = 0;
        // Off entry 0 is zero; the rest carry a unique pattern. In top.v source
        // 15 is the pure injected-current source.
        for (idx = 0; idx < 16; idx = idx + 1) begin
            if (idx == 16'd0)
                ref_word[idx] = 64'd0;
            else
                ref_word[idx] = {16'h1000 + idx, 16'h2000 + idx,
                                 16'h3000 + idx, 16'h4000 + idx};
            sources[idx*64 +: 64] = ref_word[idx];
        end

        // 1) sweep each DAC's nibble independently (other nibbles held at 0)
        for (idx = 0; idx < 16; idx = idx + 1) begin
            sel = idx[3:0];        #1; check(d0, ref_word[idx], 0);
            sel = idx[3:0] << 4;   #1; check(d1, ref_word[idx], 1);
            sel = idx[3:0] << 8;   #1; check(d2, ref_word[idx], 2);
            sel = idx[3:0] << 12;  #1; check(d3, ref_word[idx], 3);
        end

        // 2) independent routing: a distinct source on each DAC
        sel = 16'h3210; #1;
        check(d0, ref_word[0], 10); check(d1, ref_word[1], 11);
        check(d2, ref_word[2], 12); check(d3, ref_word[3], 13);
        sel = 16'hFEDC; #1;
        check(d0, ref_word[12], 20); check(d1, ref_word[13], 21);
        check(d2, ref_word[14], 22); check(d3, ref_word[15], 23);

        // 3) DDS broadcast (idx 1) routed to all four DACs simultaneously
        sel = 16'h1111; #1;
        check(d0, ref_word[1], 30); check(d1, ref_word[1], 31);
        check(d2, ref_word[1], 32); check(d3, ref_word[1], 33);

        // 4) source 15 is routable to every DAC
        sel = 16'hFFFF; #1;
        check(d0, ref_word[15], 40); check(d1, ref_word[15], 41);
        check(d2, ref_word[15], 42); check(d3, ref_word[15], 43);

        // 5) off code 0 -> zero on every DAC
        sel = 16'hF00F; #1;
        check(d0, ref_word[15], 50); check(d1, 64'd0, 51);
        check(d2, 64'd0, 52); check(d3, ref_word[15], 53);

        if (errors == 0)
            $display("TB_RESULT: PASS dac_source_crossbar (all checks)");
        else
            $display("TB_RESULT: FAIL dac_source_crossbar errors=%0d", errors);
        $finish;
    end
endmodule

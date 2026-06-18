`timescale 1ns/1ps

module adc_burst_capture_tb;
    reg clk = 1'b0;
    reg rd_clk = 1'b0;
    reg rst = 1'b1;
    reg start = 1'b0;
    reg [31:0] capture_beats = 32'd0;
    reg data_valid = 1'b1;
    reg [127:0] frame_data = 128'd0;
    reg m_axis_tready = 1'b1;

    wire [127:0] m_axis_tdata;
    wire [15:0]  m_axis_tkeep;
    wire         m_axis_tlast;
    wire         m_axis_tvalid;
    wire [31:0]  status;

    integer errors = 0;
    integer beat_count = 0;
    integer wait_cycles = 0;
    integer zero_handshakes = 0;

    always #2 clk = ~clk;       // 250 MHz
    always #1.667 rd_clk = ~rd_clk;

    always @(posedge clk) begin
        if (data_valid)
            frame_data <= frame_data + 128'h0000_0000_0000_0001_0000_0000_0000_0001;
    end

    adc_burst_capture #(
        .FIFO_DEPTH (64)
    ) dut (
        .clk           (clk),
        .rd_clk        (rd_clk),
        .rst           (rst),
        .start         (start),
        .capture_beats (capture_beats),
        .data_valid    (data_valid),
        .frame_data    (frame_data),
        .m_axis_tdata  (m_axis_tdata),
        .m_axis_tkeep  (m_axis_tkeep),
        .m_axis_tlast  (m_axis_tlast),
        .m_axis_tvalid (m_axis_tvalid),
        .m_axis_tready (m_axis_tready),
        .status        (status)
    );

    task fail(input string msg);
        begin
            $display("FAIL: %s", msg);
            errors = errors + 1;
        end
    endtask

    task pulse_start(input [31:0] beats);
        begin
            @(posedge clk);
            capture_beats <= beats;
            start <= 1'b1;
            @(posedge clk);
            start <= 1'b0;
        end
    endtask

    initial begin
        repeat (16) @(posedge clk);
        rst <= 1'b0;
        repeat (16) @(posedge clk);

        // A normal capture should produce exactly the requested number of AXIS
        // beats and assert TLAST on the final beat only.
        pulse_start(32'd4);
        beat_count = 0;
        wait_cycles = 0;
        while (beat_count < 4 && wait_cycles < 2000) begin
            @(posedge rd_clk);
            wait_cycles = wait_cycles + 1;
            if (m_axis_tvalid && m_axis_tready) begin
                if (m_axis_tkeep !== 16'hFFFF)
                    fail("unexpected tkeep");
                if (m_axis_tlast !== (beat_count == 3))
                    fail("TLAST was not aligned to the final beat");
                beat_count = beat_count + 1;
            end
        end
        if (beat_count != 4)
            fail("normal capture did not emit four beats");

        wait_cycles = 0;
        while (!status[23] && wait_cycles < 2000) begin
            @(posedge clk);
            wait_cycles = wait_cycles + 1;
        end
        if (!status[23])
            fail("normal capture did not report done");
        if (status[21])
            fail("normal capture overflowed");

        // A zero-length capture used to wedge with running=1 and no TLAST. It
        // should now finish without emitting any AXIS data.
        pulse_start(32'd0);
        zero_handshakes = 0;
        wait_cycles = 0;
        while (!status[23] && wait_cycles < 2000) begin
            @(posedge rd_clk);
            wait_cycles = wait_cycles + 1;
            if (m_axis_tvalid && m_axis_tready)
                zero_handshakes = zero_handshakes + 1;
        end
        if (!status[23])
            fail("zero-length capture did not report done");
        if (zero_handshakes != 0)
            fail("zero-length capture emitted AXIS data");
        if (status[22] || status[18])
            fail("zero-length capture remained running or AXIS-enabled");

        if (errors != 0) begin
            $display("adc_burst_capture_tb FAILED with %0d error(s)", errors);
            $fatal(1);
        end
        $display("adc_burst_capture_tb PASSED");
        $finish;
    end

    initial begin
        #200000;
        fail("testbench timeout");
        $fatal(1);
    end

    wire unused = ^m_axis_tdata;
endmodule

`timescale 1ns/1ps

// Board spike-pulse waveform RAM.
//
// Port A is the MicroBlaze/AXI BRAM-controller side: signed s16 DAC samples are
// packed two per 32-bit word.  The 32 KB AXI aperture contains four independent
// 8 KB banks (bank = axi_addr[14:13] for ADDR_W=10), one per neuron.  Each bank
// has its own 64-bit Port B read stream ({s3,s2,s1,s0}).
module spike_shape_bram_bank #(
    parameter integer ADDR_W = 10              // 1024 beats * 4 = 4096 samples
) (
    input  wire [31:0]        axi_addr,
    input  wire               axi_clk,
    input  wire [31:0]        axi_din,
    output wire [31:0]        axi_dout,
    input  wire               axi_en,
    input  wire [3:0]         axi_we,
    input  wire               fabric_clk,
    input  wire [ADDR_W-1:0]  fabric_addr0,
    output wire [63:0]        fabric_dout0,
    input  wire [ADDR_W-1:0]  fabric_addr1,
    output wire [63:0]        fabric_dout1,
    input  wire [ADDR_W-1:0]  fabric_addr2,
    output wire [63:0]        fabric_dout2,
    input  wire [ADDR_W-1:0]  fabric_addr3,
    output wire [63:0]        fabric_dout3
);
    localparam integer AXI_ADDR_W = ADDR_W + 1;
    localparam integer MEM_BITS   = (1 << ADDR_W) * 64;

    wire [31:0] spike_shape_axi_dout_rep [0:3];
    wire [63:0] spike_shape_fabric_dout_rep [0:3];
    wire [ADDR_W-1:0] spike_shape_fabric_addr_rep [0:3];

    assign spike_shape_fabric_addr_rep[0] = fabric_addr0;
    assign spike_shape_fabric_addr_rep[1] = fabric_addr1;
    assign spike_shape_fabric_addr_rep[2] = fabric_addr2;
    assign spike_shape_fabric_addr_rep[3] = fabric_addr3;
    assign fabric_dout0 = spike_shape_fabric_dout_rep[0];
    assign fabric_dout1 = spike_shape_fabric_dout_rep[1];
    assign fabric_dout2 = spike_shape_fabric_dout_rep[2];
    assign fabric_dout3 = spike_shape_fabric_dout_rep[3];

    wire [1:0] axi_bank = axi_addr[ADDR_W+4:ADDR_W+3];
    reg  [1:0] axi_bank_q = 2'd0;
    always @(posedge axi_clk) begin
        if (axi_en)
            axi_bank_q <= axi_bank;
    end
    assign axi_dout = spike_shape_axi_dout_rep[axi_bank_q];

    genvar pulse_rep;
    generate
        for (pulse_rep = 0; pulse_rep < 4; pulse_rep = pulse_rep + 1) begin : g_spike_shape_bram
            xpm_memory_tdpram #(
                .ADDR_WIDTH_A        (AXI_ADDR_W),
                .ADDR_WIDTH_B        (ADDR_W),
                .BYTE_WRITE_WIDTH_A  (8),
                .BYTE_WRITE_WIDTH_B  (8),
                .CLOCKING_MODE       ("independent_clock"),
                .MEMORY_PRIMITIVE    ("block"),
                .MEMORY_SIZE         (MEM_BITS),
                .READ_DATA_WIDTH_A   (32),
                .READ_DATA_WIDTH_B   (64),
                .READ_LATENCY_A      (1),
                .READ_LATENCY_B      (1),
                .WRITE_DATA_WIDTH_A  (32),
                .WRITE_DATA_WIDTH_B  (64),
                .WRITE_MODE_A        ("read_first"),
                .WRITE_MODE_B        ("read_first")
            ) u_spike_shape_bram (
                .clka           (axi_clk),
                .ena            (axi_en && (axi_bank == pulse_rep[1:0])),
                .wea            ((axi_bank == pulse_rep[1:0]) ? axi_we : 4'b0),
                .addra          (axi_addr[ADDR_W+2:2]),
                .dina           (axi_din),
                .douta          (spike_shape_axi_dout_rep[pulse_rep]),
                .clkb           (fabric_clk),
                .enb            (1'b1),
                .web            (8'b0),
                .addrb          (spike_shape_fabric_addr_rep[pulse_rep]),
                .dinb           (64'b0),
                .doutb          (spike_shape_fabric_dout_rep[pulse_rep]),
                .rsta           (1'b0),
                .rstb           (1'b0),
                .regcea         (1'b1),
                .regceb         (1'b1),
                .sleep          (1'b0),
                .injectsbiterra (1'b0),
                .injectdbiterra (1'b0),
                .injectsbiterrb (1'b0),
                .injectdbiterrb (1'b0)
            );
        end
    endgenerate
endmodule

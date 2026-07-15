`timescale 1ns/1ps

// Izhikevich neuron bank (runs in the slow clk_50 neuron domain).
//
// Config is loaded from a dual-clock BRAM ("config bank"): the MicroBlaze
// writes the profile image over AXI on port A; this module reads it on port B
// when a start pulse arrives (already synchronized into clk). One reader walks
// the whole BRAM each pulse, gated by the control word at address 0:
//
//   addr 0 : control   [3:0] = neuron program mask (bit n -> program neuron n)
//                       [8]   = global-set (program the global region)
//   addr 1 : global dt              (Q16.16)
//   addr 2 : global update_period   (counts of clk; low 24 bits)
//   addr 4 + n*8 + {0..5} : neuron n profile = a, b, c, d, Ic, I  (Q16.16)
//
// While a neuron is being (re)programmed it is held in reset; the others keep
// free-running and streaming spikes. Source selection and pulse shaping happen
// downstream in the GT clock domain and are NOT part of this bank.

module izh_dac_bank #(
    parameter integer ADDR_W = 6              // 64-word config BRAM
) (
    input  wire                   clk,        // clk_50 (neuron domain)
    input  wire                   reset,      // global reset, active high
    input  wire                   prog_start, // 1-cycle pulse (CDC'd to clk): load bank
    input  wire                   experiment_restart, // current-player restart: fresh v/u phase
    input  wire signed [31:0]     i_external, // injected current (Q16.16): added to each neuron's I

    output reg  [ADDR_W-1:0]      cfg_addr,   // config BRAM port-B read address
    input  wire [31:0]            cfg_data,   // config BRAM port-B read data (1-cycle latency)

    output wire [3:0]             spike_flags,    // per-neuron spike (-> GT pulse shaper)
    output wire [127:0]           i_mon,          // per-neuron current I+I_constant (Q16.16, 4x32)
    output wire [31:0]            debug_word
);
    // ---- defaults (regular-spiking; a=0.02 b=0.20 c=-65 d=8 Ic=10 I=0) -------
    localparam signed [31:0] DEF_A      = 32'sh0000_051F;
    localparam signed [31:0] DEF_B      = 32'sh0000_3333;
    localparam signed [31:0] DEF_C      = 32'shFFBF_0000;
    localparam signed [31:0] DEF_D      = 32'sh0008_0000;
    localparam signed [31:0] DEF_I      = 32'sh0000_0000;
    localparam signed [31:0] DEF_ICONST = 32'sh000A_0000;
    localparam signed [31:0] DEF_DT     = 32'sh0000_1000; // 0.0625
    localparam [23:0]        DEF_PERIOD = 24'd256;        // ~5.12 us @ 50 MHz

    // ---- config registers ----------------------------------------------------
    reg signed [31:0] a_param [0:3];
    reg signed [31:0] b_param [0:3];
    reg signed [31:0] c_param [0:3];
    reg signed [31:0] d_param [0:3];
    reg signed [31:0] i_param [0:3];
    reg signed [31:0] i_const [0:3];
    reg signed [31:0] g_dt;
    reg [23:0]        g_period;
    reg [3:0]         neuron_reset;

    // ---- BRAM layout constants ----------------------------------------------
    localparam [ADDR_W-1:0] A_CTRL   = 6'd0;
    localparam [ADDR_W-1:0] A_DT     = 6'd1;
    localparam [ADDR_W-1:0] A_PERIOD = 6'd2;
    localparam [ADDR_W-1:0] A_NBASE  = 6'd4;
    localparam integer      NSTRIDE  = 8;
    localparam [ADDR_W-1:0] A_LAST   = A_NBASE + 4 * NSTRIDE - 1;  // 35

    // ---- reader FSM (2 cycles/word: address then registered data) -----------
    localparam [2:0] S_IDLE = 3'd0, S_WAIT = 3'd1, S_READ = 3'd2, S_DONE = 3'd3;
    reg [2:0]        st;
    reg [ADDR_W-1:0] idx;
    reg [3:0]        mask;
    reg              global_set;

    // Decode the neuron/param index from the offset INTO the neuron region
    // (idx - A_NBASE).  Must subtract A_NBASE first: the firmware writes
    // neuron n at words A_NBASE + n*NSTRIDE + {0..5}, so e.g. neuron 0's a is
    // at idx=4, which is offset 0 -> nsel=0, psel=0.  (Earlier this decoded
    // straight off idx, an off-by-A_NBASE bug that left every neuron's a/b/c/d
    // at their reset defaults -> profiles had no effect.)
    wire [ADDR_W-1:0] noff = idx - A_NBASE;
    wire [1:0] nsel = noff[4:3];              // which neuron (0..3)
    wire [2:0] psel = noff[2:0];              // which param: 0..5 = a,b,c,d,Ic,I
    wire       in_neuron = (idx >= A_NBASE) && (psel <= 3'd5);

    integer k;
    always @(posedge clk) begin
        if (reset) begin
            for (k = 0; k < 4; k = k + 1) begin
                a_param[k] <= DEF_A; b_param[k] <= DEF_B;
                c_param[k] <= DEF_C; d_param[k] <= DEF_D;
                i_param[k] <= DEF_I; i_const[k] <= DEF_ICONST;
            end
            g_dt <= DEF_DT; g_period <= DEF_PERIOD;
            neuron_reset <= 4'hF;
            st <= S_IDLE; idx <= 0; cfg_addr <= 0; mask <= 0; global_set <= 0;
        end else begin
            case (st)
                S_IDLE: begin
                    neuron_reset <= 4'h0;          // all neurons free-run
                    if (prog_start) begin
                        idx <= A_CTRL; cfg_addr <= A_CTRL; st <= S_WAIT;
                    end
                end
                S_WAIT: st <= S_READ;              // 1-cycle BRAM read latency
                S_READ: begin
                    if (idx == A_CTRL) begin
                        mask <= cfg_data[3:0];
                        global_set <= cfg_data[8];
                        neuron_reset <= cfg_data[3:0];     // hold masked in reset
                    end else if (idx == A_DT && global_set) begin
                        g_dt <= cfg_data;
                    end else if (idx == A_PERIOD && global_set) begin
                        g_period <= cfg_data[23:0];
                    end else if (in_neuron && mask[nsel]) begin
                        case (psel)
                        3'd0: a_param[nsel] <= cfg_data;
                        3'd1: b_param[nsel] <= cfg_data;
                        3'd2: c_param[nsel] <= cfg_data;
                        3'd3: d_param[nsel] <= cfg_data;
                        3'd4: i_const[nsel] <= cfg_data;   // Ic
                        3'd5: i_param[nsel] <= cfg_data;   // I
                        default: ;
                        endcase
                    end

                    if (idx == A_LAST) begin
                        st <= S_DONE;
                    end else begin
                        idx <= idx + 1'b1; cfg_addr <= idx + 1'b1; st <= S_WAIT;
                    end
                end
                S_DONE: begin
                    neuron_reset <= 4'h0;          // release the reprogrammed neurons
                    st <= S_IDLE;
                end
                default: st <= S_IDLE;
            endcase
        end
    end

    // ---- neurons (core explicitly emits SPIKE; spikes go to the GT shaper) ---
    wire [3:0]         spike;
    wire [3:0]         effective_neuron_reset = neuron_reset |
                                                  {4{reset | experiment_restart}};
    wire signed [31:0] v_out [0:3];
    wire signed [31:0] u_out [0:3];

    genvar n;
    generate
        for (n = 0; n < 4; n = n + 1) begin : gen_neuron
            reg [23:0] upd_cnt = 24'd0;
            wire [23:0] reload = (g_period <= 24'd1) ? 24'd0 : (g_period - 1'b1);
            wire step = (upd_cnt == 24'd0);

            always @(posedge clk) begin
                if (effective_neuron_reset[n] | (reload == 24'd0))
                    upd_cnt <= 24'd0;
                else if (upd_cnt == reload)
                    upd_cnt <= 24'd0;
                else
                    upd_cnt <= upd_cnt + 1'b1;
            end

            izh_neuron u_izh_neuron (
                .clk        (clk),
                .reset      (effective_neuron_reset[n]),
                .a_param    (a_param[n]),
                .b_param    (b_param[n]),
                .c_param    (c_param[n]),
                .d_param    (d_param[n]),
                .I          (i_param[n] + i_external),   // static bias + injected current source
                .v_timestep (g_dt),
                .I_constant (i_const[n]),
                .step_enable(step),
                .SPIKE      (spike[n]),
                .v_out      (v_out[n]),
                .u_out      (u_out[n])
            );
        end
    endgenerate

    wire busy = (st != S_IDLE);
    // The third-party core's SPIKE register does not explicitly test reset and
    // can otherwise expose one stale event on a v/u reset edge.  Suppress that
    // cycle along with resetting v/u and the update divider.
    assign spike_flags = spike & ~effective_neuron_reset;
    assign debug_word  = {8'h1A, mask, busy, global_set, v_out[0][17:0]};

    // Per-neuron current monitor = exactly what each neuron integrates
    // (I + I_constant = i_param + i_external + i_const).  Tapped here so a DAC
    // can mirror it through the GT-domain sample-and-hold CDC.
    genvar mn;
    generate
        for (mn = 0; mn < 4; mn = mn + 1) begin : g_imon
            assign i_mon[mn*32 +: 32] = i_param[mn] + i_external + i_const[mn];
        end
    endgenerate

endmodule

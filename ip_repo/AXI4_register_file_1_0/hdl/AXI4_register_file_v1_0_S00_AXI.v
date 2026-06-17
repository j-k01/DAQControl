
`timescale 1 ns / 1 ps

	module AXI4_register_file_v1_0_S00_AXI #
	(
		parameter integer C_S_AXI_DATA_WIDTH	= 32,
		// Address width must cover NUM_REG words: C_S_AXI_ADDR_WIDTH >=
		// $clog2(NUM_REG) + ADDR_LSB.  7 -> up to 32 regs; bump for more.
		parameter integer C_S_AXI_ADDR_WIDTH	= 7,
		parameter integer NUM_REG               = 32
	)
	(
		// --- Unified register bank (flattened so the count is one parameter) ---
		// Every register is AXI read/write storage with an optional fabric write
		// port.  Registers are CONTIGUOUS: register i lives at byte offset i*4.
		//   REG        : current value of each register (read by fabric AND AXI)
		//   REG_IN/_WE : fabric write port.  REG_WE[i]=1 makes the fabric own
		//                register i (it reads as "read-only" to the CPU); leave
		//                REG_WE[i]=0 (port unconnected) for a normal RW register.
		//   REG_RDINT  : 1-cycle pulse when the CPU reads register i.
		// Fabric writes take priority over AXI writes for the same register.
		output wire [NUM_REG*C_S_AXI_DATA_WIDTH-1:0] REG,
		input  wire [NUM_REG*C_S_AXI_DATA_WIDTH-1:0] REG_IN,
		input  wire [NUM_REG-1:0]                    REG_WE,
		output wire [NUM_REG-1:0]                    REG_RDINT,

		// --- AXI Slave Interface ---
		input wire  S_AXI_ACLK,
		input wire  S_AXI_ARESETN,
		input wire [C_S_AXI_ADDR_WIDTH-1 : 0] S_AXI_AWADDR,
		input wire [2 : 0] S_AXI_AWPROT,
		input wire  S_AXI_AWVALID,
		output wire  S_AXI_AWREADY,
		input wire [C_S_AXI_DATA_WIDTH-1 : 0] S_AXI_WDATA,
		input wire [(C_S_AXI_DATA_WIDTH/8)-1 : 0] S_AXI_WSTRB,
		input wire  S_AXI_WVALID,
		output wire  S_AXI_WREADY,
		output wire [1 : 0] S_AXI_BRESP,
		output wire  S_AXI_BVALID,
		input wire  S_AXI_BREADY,
		input wire [C_S_AXI_ADDR_WIDTH-1 : 0] S_AXI_ARADDR,
		input wire [2 : 0] S_AXI_ARPROT,
		input wire  S_AXI_ARVALID,
		output wire  S_AXI_ARREADY,
		output wire [C_S_AXI_DATA_WIDTH-1 : 0] S_AXI_RDATA,
		output wire [1 : 0] S_AXI_RRESP,
		output wire  S_AXI_RVALID,
		input wire  S_AXI_RREADY
	);

	// AXI4LITE signals
	reg [C_S_AXI_ADDR_WIDTH-1 : 0] 	axi_awaddr;
	reg  	axi_awready;
	reg  	axi_wready;
	reg [1 : 0] 	axi_bresp;
	reg  	axi_bvalid;
	reg [C_S_AXI_ADDR_WIDTH-1 : 0] 	axi_araddr;
	reg  	axi_arready;
	reg [C_S_AXI_DATA_WIDTH-1 : 0] 	axi_rdata;
	reg [1 : 0] 	axi_rresp;
	reg  	axi_rvalid;

	localparam integer ADDR_LSB = (C_S_AXI_DATA_WIDTH/32) + 1;          // word-align: 2
	localparam integer IDX_BITS = C_S_AXI_ADDR_WIDTH - ADDR_LSB;        // register-index bits

	wire	 slv_reg_rden;
	wire	 slv_reg_wren;
	reg [C_S_AXI_DATA_WIDTH-1:0]	 reg_data_out;
	reg	 aw_en;

	wire [IDX_BITS-1:0] wr_index = axi_awaddr[ADDR_LSB +: IDX_BITS];
	wire [IDX_BITS-1:0] rd_index = axi_araddr[ADDR_LSB +: IDX_BITS];

	// AXI output assignments
	assign S_AXI_AWREADY	= axi_awready;
	assign S_AXI_WREADY	= axi_wready;
	assign S_AXI_BRESP	= axi_bresp;
	assign S_AXI_BVALID	= axi_bvalid;
	assign S_AXI_ARREADY	= axi_arready;
	assign S_AXI_RDATA	= axi_rdata;
	assign S_AXI_RRESP	= axi_rresp;
	assign S_AXI_RVALID	= axi_rvalid;

	// axi_awready generation
	always @( posedge S_AXI_ACLK )
	begin
	  if ( S_AXI_ARESETN == 1'b0 )
	    begin
	      axi_awready <= 1'b0;
	      aw_en <= 1'b1;
	    end
	  else
	    begin
	      if (~axi_awready && S_AXI_AWVALID && S_AXI_WVALID && aw_en)
	        begin
	          axi_awready <= 1'b1;
	          aw_en <= 1'b0;
	        end
	        else if (S_AXI_BREADY && axi_bvalid)
	            begin
	              aw_en <= 1'b1;
	              axi_awready <= 1'b0;
	            end
	      else
	        begin
	          axi_awready <= 1'b0;
	        end
	    end
	end

	// axi_awaddr latching
	always @( posedge S_AXI_ACLK )
	begin
	  if ( S_AXI_ARESETN == 1'b0 )
	    begin
	      axi_awaddr <= 0;
	    end
	  else
	    begin
	      if (~axi_awready && S_AXI_AWVALID && S_AXI_WVALID && aw_en)
	        begin
	          axi_awaddr <= S_AXI_AWADDR;
	        end
	    end
	end

	// axi_wready generation
	always @( posedge S_AXI_ACLK )
	begin
	  if ( S_AXI_ARESETN == 1'b0 )
	    begin
	      axi_wready <= 1'b0;
	    end
	  else
	    begin
	      if (~axi_wready && S_AXI_WVALID && S_AXI_AWVALID && aw_en )
	        begin
	          axi_wready <= 1'b1;
	        end
	      else
	        begin
	          axi_wready <= 1'b0;
	        end
	    end
	end

	assign slv_reg_wren = axi_wready && S_AXI_WVALID && axi_awready && S_AXI_AWVALID;

	// --- The register bank: one uniform RW register per index, with an optional
	//     fabric override (REG_WE).  Fabric write wins over an AXI write. --------
	genvar gi;
	generate
	  for (gi = 0; gi < NUM_REG; gi = gi + 1) begin : g_regs
	    reg [C_S_AXI_DATA_WIDTH-1:0] slv_reg;
	    reg                          rdint_r;
	    integer                      b;

	    assign REG[gi*C_S_AXI_DATA_WIDTH +: C_S_AXI_DATA_WIDTH] = slv_reg;
	    assign REG_RDINT[gi] = rdint_r;

	    always @( posedge S_AXI_ACLK )
	    begin
	      if ( S_AXI_ARESETN == 1'b0 )
	        slv_reg <= 0;
	      else if ( REG_WE[gi] )
	        slv_reg <= REG_IN[gi*C_S_AXI_DATA_WIDTH +: C_S_AXI_DATA_WIDTH];
	      else if ( slv_reg_wren && (wr_index == gi) )
	        for ( b = 0; b <= (C_S_AXI_DATA_WIDTH/8)-1; b = b+1 )
	          if ( S_AXI_WSTRB[b] == 1 )
	            slv_reg[(b*8) +: 8] <= S_AXI_WDATA[(b*8) +: 8];
	    end

	    always @( posedge S_AXI_ACLK )
	    begin
	      if ( S_AXI_ARESETN == 1'b0 )
	        rdint_r <= 1'b0;
	      else
	        rdint_r <= slv_reg_rden && (rd_index == gi);
	    end
	  end
	endgenerate

	// Write response logic
	always @( posedge S_AXI_ACLK )
	begin
	  if ( S_AXI_ARESETN == 1'b0 )
	    begin
	      axi_bvalid  <= 0;
	      axi_bresp   <= 2'b0;
	    end
	  else
	    begin
	      if (axi_awready && S_AXI_AWVALID && ~axi_bvalid && axi_wready && S_AXI_WVALID)
	        begin
	          axi_bvalid <= 1'b1;
	          axi_bresp  <= 2'b0;
	        end
	      else
	        begin
	          if (S_AXI_BREADY && axi_bvalid)
	            begin
	              axi_bvalid <= 1'b0;
	            end
	        end
	    end
	end

	// axi_arready generation
	always @( posedge S_AXI_ACLK )
	begin
	  if ( S_AXI_ARESETN == 1'b0 )
	    begin
	      axi_arready <= 1'b0;
	      axi_araddr  <= 0;
	    end
	  else
	    begin
	      if (~axi_arready && S_AXI_ARVALID)
	        begin
	          axi_arready <= 1'b1;
	          axi_araddr  <= S_AXI_ARADDR;
	        end
	      else
	        begin
	          axi_arready <= 1'b0;
	        end
	    end
	end

	// axi_rvalid generation
	always @( posedge S_AXI_ACLK )
	begin
	  if ( S_AXI_ARESETN == 1'b0 )
	    begin
	      axi_rvalid <= 0;
	      axi_rresp  <= 0;
	    end
	  else
	    begin
	      if (axi_arready && S_AXI_ARVALID && ~axi_rvalid)
	        begin
	          axi_rvalid <= 1'b1;
	          axi_rresp  <= 2'b0;
	        end
	      else if (axi_rvalid && S_AXI_RREADY)
	        begin
	          axi_rvalid <= 1'b0;
	        end
	    end
	end

	// Read mux — register at rd_index (contiguous; out-of-range reads as 0)
	assign slv_reg_rden = axi_arready & S_AXI_ARVALID & ~axi_rvalid;
	always @(*)
	begin
	  if ( rd_index < NUM_REG )
	    reg_data_out = REG[rd_index*C_S_AXI_DATA_WIDTH +: C_S_AXI_DATA_WIDTH];
	  else
	    reg_data_out = 0;
	end

	// Output register read data
	always @( posedge S_AXI_ACLK )
	begin
	  if ( S_AXI_ARESETN == 1'b0 )
	    begin
	      axi_rdata  <= 0;
	    end
	  else
	    begin
	      if (slv_reg_rden)
	        begin
	          axi_rdata <= reg_data_out;
	        end
	    end
	end

	endmodule

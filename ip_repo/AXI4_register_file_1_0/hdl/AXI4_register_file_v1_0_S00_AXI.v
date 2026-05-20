
`timescale 1 ns / 1 ps

	module AXI4_register_file_v1_0_S00_AXI #
	(
		parameter integer C_S_AXI_DATA_WIDTH	= 32,
		parameter integer C_S_AXI_ADDR_WIDTH	= 5
	)
	(
		// --- Fabric-facing ports ---
		// RW regs: AXI can read/write, fabric can read
		output wire [C_S_AXI_DATA_WIDTH-1:0] RW_REG0,
		output wire [C_S_AXI_DATA_WIDTH-1:0] RW_REG1,
		output wire [C_S_AXI_DATA_WIDTH-1:0] RW_REG2,
		output wire [C_S_AXI_DATA_WIDTH-1:0] RW_REG3,
		// RO regs: AXI can only read, fabric writes with WE
		input wire [C_S_AXI_DATA_WIDTH-1:0] RO_REG0_IN,
		input wire                           RO_REG0_WE,
		input wire [C_S_AXI_DATA_WIDTH-1:0] RO_REG1_IN,
		input wire                           RO_REG1_WE,
		input wire [C_S_AXI_DATA_WIDTH-1:0] RO_REG2_IN,
		input wire                           RO_REG2_WE,
		input wire [C_S_AXI_DATA_WIDTH-1:0] RO_REG3_IN,
		input wire                           RO_REG3_WE,
		// Read strobes: one-cycle pulse when MicroBlaze reads an RO reg
		output wire                          RO_REG0_RDINT,
		output wire                          RO_REG1_RDINT,
		output wire                          RO_REG2_RDINT,
		output wire                          RO_REG3_RDINT,

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

	localparam integer ADDR_LSB = (C_S_AXI_DATA_WIDTH/32) + 1;
	localparam integer OPT_MEM_ADDR_BITS = 2;

	// 8 registers: 4 RW + 4 RO
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_rw_reg0;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_rw_reg1;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_rw_reg2;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_rw_reg3;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_ro_reg0;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_ro_reg1;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_ro_reg2;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_ro_reg3;

	wire	 slv_reg_rden;
	wire	 slv_reg_wren;
	reg [C_S_AXI_DATA_WIDTH-1:0]	 reg_data_out;
	integer	 byte_index;
	reg	 aw_en;

	// Fabric read outputs
	assign RW_REG0 = slv_rw_reg0;
	assign RW_REG1 = slv_rw_reg1;
	assign RW_REG2 = slv_rw_reg2;
	assign RW_REG3 = slv_rw_reg3;

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

	// Write logic — only RW regs (0-3) accept AXI writes
	// RO regs (4-7) are written by fabric only
	assign slv_reg_wren = axi_wready && S_AXI_WVALID && axi_awready && S_AXI_AWVALID;

	always @( posedge S_AXI_ACLK )
	begin
	  if ( S_AXI_ARESETN == 1'b0 )
	    begin
	      slv_rw_reg0 <= 0;
	      slv_rw_reg1 <= 0;
	      slv_rw_reg2 <= 0;
	      slv_rw_reg3 <= 0;
	      slv_ro_reg0 <= 0;
	      slv_ro_reg1 <= 0;
	      slv_ro_reg2 <= 0;
	      slv_ro_reg3 <= 0;
	    end
	  else begin
	    // Fabric writes to RO registers (active-high WE, one clock)
	    if (RO_REG0_WE) slv_ro_reg0 <= RO_REG0_IN;
	    if (RO_REG1_WE) slv_ro_reg1 <= RO_REG1_IN;
	    if (RO_REG2_WE) slv_ro_reg2 <= RO_REG2_IN;
	    if (RO_REG3_WE) slv_ro_reg3 <= RO_REG3_IN;

	    // AXI writes to RW registers
	    if (slv_reg_wren)
	      begin
	        case ( axi_awaddr[ADDR_LSB+OPT_MEM_ADDR_BITS:ADDR_LSB] )
	          3'h0:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 )
	                slv_rw_reg0[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	          3'h1:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 )
	                slv_rw_reg1[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	          3'h2:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 )
	                slv_rw_reg2[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	          3'h3:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 )
	                slv_rw_reg3[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	          // 3'h4 - 3'h7: RO regs, AXI writes are silently ignored
	          default : ;
	        endcase
	      end
	  end
	end

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

	// Read logic — all 8 registers readable from AXI
	assign slv_reg_rden = axi_arready & S_AXI_ARVALID & ~axi_rvalid;
	always @(*)
	begin
	      case ( axi_araddr[ADDR_LSB+OPT_MEM_ADDR_BITS:ADDR_LSB] )
	        3'h0   : reg_data_out <= slv_rw_reg0;
	        3'h1   : reg_data_out <= slv_rw_reg1;
	        3'h2   : reg_data_out <= slv_rw_reg2;
	        3'h3   : reg_data_out <= slv_rw_reg3;
	        3'h4   : reg_data_out <= slv_ro_reg0;
	        3'h5   : reg_data_out <= slv_ro_reg1;
	        3'h6   : reg_data_out <= slv_ro_reg2;
	        3'h7   : reg_data_out <= slv_ro_reg3;
	        default : reg_data_out <= 0;
	      endcase
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

	// RO register read strobes — pulse for one cycle on AXI read
	reg ro_reg0_rdint_r, ro_reg1_rdint_r, ro_reg2_rdint_r, ro_reg3_rdint_r;
	assign RO_REG0_RDINT = ro_reg0_rdint_r;
	assign RO_REG1_RDINT = ro_reg1_rdint_r;
	assign RO_REG2_RDINT = ro_reg2_rdint_r;
	assign RO_REG3_RDINT = ro_reg3_rdint_r;

	always @( posedge S_AXI_ACLK )
	begin
	  if ( S_AXI_ARESETN == 1'b0 )
	    begin
	      ro_reg0_rdint_r <= 1'b0;
	      ro_reg1_rdint_r <= 1'b0;
	      ro_reg2_rdint_r <= 1'b0;
	      ro_reg3_rdint_r <= 1'b0;
	    end
	  else
	    begin
	      ro_reg0_rdint_r <= 1'b0;
	      ro_reg1_rdint_r <= 1'b0;
	      ro_reg2_rdint_r <= 1'b0;
	      ro_reg3_rdint_r <= 1'b0;
	      if (slv_reg_rden)
	        case ( axi_araddr[ADDR_LSB+OPT_MEM_ADDR_BITS:ADDR_LSB] )
	          3'h4 : ro_reg0_rdint_r <= 1'b1;
	          3'h5 : ro_reg1_rdint_r <= 1'b1;
	          3'h6 : ro_reg2_rdint_r <= 1'b1;
	          3'h7 : ro_reg3_rdint_r <= 1'b1;
	          default : ;
	        endcase
	    end
	end

	endmodule

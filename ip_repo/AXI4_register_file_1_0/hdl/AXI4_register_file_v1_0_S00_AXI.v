
`timescale 1 ns / 1 ps

	module AXI4_register_file_v1_0_S00_AXI #
	(
		parameter integer C_S_AXI_DATA_WIDTH	= 32,
		parameter integer C_S_AXI_ADDR_WIDTH	= 7
	)
	(
		// --- Fabric-facing ports ---
		// Drop-in extension of the 8-RW register file to 16 RW.  Reg index =
		// araddr[6:2]; byte offset = index*4.  Layout (unchanged RW0-7 / RO0-7):
		//   RW0-7  : index 0x00-0x07  (byte 0x00-0x1c)
		//   RO0-7  : index 0x08-0x0f  (byte 0x20-0x3c)  -- read-only, unchanged
		//   RW8-15 : index 0x10-0x17  (byte 0x40-0x5c)  -- NEW
		output wire [C_S_AXI_DATA_WIDTH-1:0] RW_REG0,
		output wire [C_S_AXI_DATA_WIDTH-1:0] RW_REG1,
		output wire [C_S_AXI_DATA_WIDTH-1:0] RW_REG2,
		output wire [C_S_AXI_DATA_WIDTH-1:0] RW_REG3,
		output wire [C_S_AXI_DATA_WIDTH-1:0] RW_REG4,
		output wire [C_S_AXI_DATA_WIDTH-1:0] RW_REG5,
		output wire [C_S_AXI_DATA_WIDTH-1:0] RW_REG6,
		output wire [C_S_AXI_DATA_WIDTH-1:0] RW_REG7,
		output wire [C_S_AXI_DATA_WIDTH-1:0] RW_REG8,
		output wire [C_S_AXI_DATA_WIDTH-1:0] RW_REG9,
		output wire [C_S_AXI_DATA_WIDTH-1:0] RW_REG10,
		output wire [C_S_AXI_DATA_WIDTH-1:0] RW_REG11,
		output wire [C_S_AXI_DATA_WIDTH-1:0] RW_REG12,
		output wire [C_S_AXI_DATA_WIDTH-1:0] RW_REG13,
		output wire [C_S_AXI_DATA_WIDTH-1:0] RW_REG14,
		output wire [C_S_AXI_DATA_WIDTH-1:0] RW_REG15,
		// RO regs: AXI can only read, fabric writes with WE
		input wire [C_S_AXI_DATA_WIDTH-1:0] RO_REG0_IN,
		input wire                           RO_REG0_WE,
		input wire [C_S_AXI_DATA_WIDTH-1:0] RO_REG1_IN,
		input wire                           RO_REG1_WE,
		input wire [C_S_AXI_DATA_WIDTH-1:0] RO_REG2_IN,
		input wire                           RO_REG2_WE,
		input wire [C_S_AXI_DATA_WIDTH-1:0] RO_REG3_IN,
		input wire                           RO_REG3_WE,
		input wire [C_S_AXI_DATA_WIDTH-1:0] RO_REG4_IN,
		input wire                           RO_REG4_WE,
		input wire [C_S_AXI_DATA_WIDTH-1:0] RO_REG5_IN,
		input wire                           RO_REG5_WE,
		input wire [C_S_AXI_DATA_WIDTH-1:0] RO_REG6_IN,
		input wire                           RO_REG6_WE,
		input wire [C_S_AXI_DATA_WIDTH-1:0] RO_REG7_IN,
		input wire                           RO_REG7_WE,
		// Read strobes: one-cycle pulse when MicroBlaze reads an RO reg
		output wire                          RO_REG0_RDINT,
		output wire                          RO_REG1_RDINT,
		output wire                          RO_REG2_RDINT,
		output wire                          RO_REG3_RDINT,
		output wire                          RO_REG4_RDINT,
		output wire                          RO_REG5_RDINT,
		output wire                          RO_REG6_RDINT,
		output wire                          RO_REG7_RDINT,

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
	localparam integer OPT_MEM_ADDR_BITS = 4;   // 5-bit reg index -> up to 32 regs

	// 24 registers: 16 RW (0x00-0x0f) + 8 RO (0x10-0x17)
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_rw_reg0;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_rw_reg1;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_rw_reg2;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_rw_reg3;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_rw_reg4;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_rw_reg5;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_rw_reg6;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_rw_reg7;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_rw_reg8;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_rw_reg9;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_rw_reg10;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_rw_reg11;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_rw_reg12;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_rw_reg13;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_rw_reg14;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_rw_reg15;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_ro_reg0;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_ro_reg1;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_ro_reg2;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_ro_reg3;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_ro_reg4;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_ro_reg5;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_ro_reg6;
	reg [C_S_AXI_DATA_WIDTH-1:0]	slv_ro_reg7;

	wire	 slv_reg_rden;
	wire	 slv_reg_wren;
	reg [C_S_AXI_DATA_WIDTH-1:0]	 reg_data_out;
	integer	 byte_index;
	reg	 aw_en;

	// Fabric read outputs
	assign RW_REG0  = slv_rw_reg0;
	assign RW_REG1  = slv_rw_reg1;
	assign RW_REG2  = slv_rw_reg2;
	assign RW_REG3  = slv_rw_reg3;
	assign RW_REG4  = slv_rw_reg4;
	assign RW_REG5  = slv_rw_reg5;
	assign RW_REG6  = slv_rw_reg6;
	assign RW_REG7  = slv_rw_reg7;
	assign RW_REG8  = slv_rw_reg8;
	assign RW_REG9  = slv_rw_reg9;
	assign RW_REG10 = slv_rw_reg10;
	assign RW_REG11 = slv_rw_reg11;
	assign RW_REG12 = slv_rw_reg12;
	assign RW_REG13 = slv_rw_reg13;
	assign RW_REG14 = slv_rw_reg14;
	assign RW_REG15 = slv_rw_reg15;

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

	// Write logic — RW regs accept AXI writes; RO regs are fabric-written only
	assign slv_reg_wren = axi_wready && S_AXI_WVALID && axi_awready && S_AXI_AWVALID;

	always @( posedge S_AXI_ACLK )
	begin
	  if ( S_AXI_ARESETN == 1'b0 )
	    begin
	      slv_rw_reg0 <= 0;
	      slv_rw_reg1 <= 0;
	      slv_rw_reg2 <= 0;
	      slv_rw_reg3 <= 0;
	      slv_rw_reg4 <= 0;
	      slv_rw_reg5 <= 0;
	      slv_rw_reg6 <= 0;
	      slv_rw_reg7 <= 0;
	      slv_rw_reg8 <= 0;
	      slv_rw_reg9 <= 0;
	      slv_rw_reg10 <= 0;
	      slv_rw_reg11 <= 0;
	      slv_rw_reg12 <= 0;
	      slv_rw_reg13 <= 0;
	      slv_rw_reg14 <= 0;
	      slv_rw_reg15 <= 0;
	      slv_ro_reg0 <= 0;
	      slv_ro_reg1 <= 0;
	      slv_ro_reg2 <= 0;
	      slv_ro_reg3 <= 0;
	      slv_ro_reg4 <= 0;
	      slv_ro_reg5 <= 0;
	      slv_ro_reg6 <= 0;
	      slv_ro_reg7 <= 0;
	    end
	  else begin
	    // Fabric writes to RO registers (active-high WE, one clock)
	    if (RO_REG0_WE) slv_ro_reg0 <= RO_REG0_IN;
	    if (RO_REG1_WE) slv_ro_reg1 <= RO_REG1_IN;
	    if (RO_REG2_WE) slv_ro_reg2 <= RO_REG2_IN;
	    if (RO_REG3_WE) slv_ro_reg3 <= RO_REG3_IN;
	    if (RO_REG4_WE) slv_ro_reg4 <= RO_REG4_IN;
	    if (RO_REG5_WE) slv_ro_reg5 <= RO_REG5_IN;
	    if (RO_REG6_WE) slv_ro_reg6 <= RO_REG6_IN;
	    if (RO_REG7_WE) slv_ro_reg7 <= RO_REG7_IN;

	    // AXI writes to RW registers (reg index = araddr[ADDR_LSB+4:ADDR_LSB])
	    if (slv_reg_wren)
	      begin
	        case ( axi_awaddr[ADDR_LSB+OPT_MEM_ADDR_BITS:ADDR_LSB] )
	          5'h00:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 )
	                slv_rw_reg0[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	          5'h01:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 )
	                slv_rw_reg1[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	          5'h02:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 )
	                slv_rw_reg2[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	          5'h03:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 )
	                slv_rw_reg3[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	          5'h04:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 )
	                slv_rw_reg4[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	          5'h05:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 )
	                slv_rw_reg5[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	          5'h06:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 )
	                slv_rw_reg6[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	          5'h07:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 )
	                slv_rw_reg7[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	          // 5'h08 - 5'h0f: RO regs, AXI writes are silently ignored
	          5'h10:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 )
	                slv_rw_reg8[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	          5'h11:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 )
	                slv_rw_reg9[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	          5'h12:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 )
	                slv_rw_reg10[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	          5'h13:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 )
	                slv_rw_reg11[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	          5'h14:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 )
	                slv_rw_reg12[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	          5'h15:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 )
	                slv_rw_reg13[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	          5'h16:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 )
	                slv_rw_reg14[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
	          5'h17:
	            for ( byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index+1 )
	              if ( S_AXI_WSTRB[byte_index] == 1 )
	                slv_rw_reg15[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
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

	// Read logic — RW0-7 @ index 0x00-0x07, RO0-7 @ 0x08-0x0f, RW8-15 @ 0x10-0x17
	assign slv_reg_rden = axi_arready & S_AXI_ARVALID & ~axi_rvalid;
	always @(*)
	begin
	      case ( axi_araddr[ADDR_LSB+OPT_MEM_ADDR_BITS:ADDR_LSB] )
	        5'h00 : reg_data_out <= slv_rw_reg0;
	        5'h01 : reg_data_out <= slv_rw_reg1;
	        5'h02 : reg_data_out <= slv_rw_reg2;
	        5'h03 : reg_data_out <= slv_rw_reg3;
	        5'h04 : reg_data_out <= slv_rw_reg4;
	        5'h05 : reg_data_out <= slv_rw_reg5;
	        5'h06 : reg_data_out <= slv_rw_reg6;
	        5'h07 : reg_data_out <= slv_rw_reg7;
	        5'h08 : reg_data_out <= slv_ro_reg0;
	        5'h09 : reg_data_out <= slv_ro_reg1;
	        5'h0a : reg_data_out <= slv_ro_reg2;
	        5'h0b : reg_data_out <= slv_ro_reg3;
	        5'h0c : reg_data_out <= slv_ro_reg4;
	        5'h0d : reg_data_out <= slv_ro_reg5;
	        5'h0e : reg_data_out <= slv_ro_reg6;
	        5'h0f : reg_data_out <= slv_ro_reg7;
	        5'h10 : reg_data_out <= slv_rw_reg8;
	        5'h11 : reg_data_out <= slv_rw_reg9;
	        5'h12 : reg_data_out <= slv_rw_reg10;
	        5'h13 : reg_data_out <= slv_rw_reg11;
	        5'h14 : reg_data_out <= slv_rw_reg12;
	        5'h15 : reg_data_out <= slv_rw_reg13;
	        5'h16 : reg_data_out <= slv_rw_reg14;
	        5'h17 : reg_data_out <= slv_rw_reg15;
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

	// RO register read strobes — pulse for one cycle on AXI read (RO at 0x08-0x0f)
	reg ro_reg0_rdint_r, ro_reg1_rdint_r, ro_reg2_rdint_r, ro_reg3_rdint_r;
	reg ro_reg4_rdint_r, ro_reg5_rdint_r, ro_reg6_rdint_r, ro_reg7_rdint_r;
	assign RO_REG0_RDINT = ro_reg0_rdint_r;
	assign RO_REG1_RDINT = ro_reg1_rdint_r;
	assign RO_REG2_RDINT = ro_reg2_rdint_r;
	assign RO_REG3_RDINT = ro_reg3_rdint_r;
	assign RO_REG4_RDINT = ro_reg4_rdint_r;
	assign RO_REG5_RDINT = ro_reg5_rdint_r;
	assign RO_REG6_RDINT = ro_reg6_rdint_r;
	assign RO_REG7_RDINT = ro_reg7_rdint_r;

	always @( posedge S_AXI_ACLK )
	begin
	  if ( S_AXI_ARESETN == 1'b0 )
	    begin
	      ro_reg0_rdint_r <= 1'b0;
	      ro_reg1_rdint_r <= 1'b0;
	      ro_reg2_rdint_r <= 1'b0;
	      ro_reg3_rdint_r <= 1'b0;
	      ro_reg4_rdint_r <= 1'b0;
	      ro_reg5_rdint_r <= 1'b0;
	      ro_reg6_rdint_r <= 1'b0;
	      ro_reg7_rdint_r <= 1'b0;
	    end
	  else
	    begin
	      ro_reg0_rdint_r <= 1'b0;
	      ro_reg1_rdint_r <= 1'b0;
	      ro_reg2_rdint_r <= 1'b0;
	      ro_reg3_rdint_r <= 1'b0;
	      ro_reg4_rdint_r <= 1'b0;
	      ro_reg5_rdint_r <= 1'b0;
	      ro_reg6_rdint_r <= 1'b0;
	      ro_reg7_rdint_r <= 1'b0;
	      if (slv_reg_rden)
	        case ( axi_araddr[ADDR_LSB+OPT_MEM_ADDR_BITS:ADDR_LSB] )
	          5'h08 : ro_reg0_rdint_r <= 1'b1;
	          5'h09 : ro_reg1_rdint_r <= 1'b1;
	          5'h0a : ro_reg2_rdint_r <= 1'b1;
	          5'h0b : ro_reg3_rdint_r <= 1'b1;
	          5'h0c : ro_reg4_rdint_r <= 1'b1;
	          5'h0d : ro_reg5_rdint_r <= 1'b1;
	          5'h0e : ro_reg6_rdint_r <= 1'b1;
	          5'h0f : ro_reg7_rdint_r <= 1'b1;
	          default : ;
	        endcase
	    end
	end

	endmodule

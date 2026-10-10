In vtop_vitap_agent create a file     
  name voice_input.py and use windsurf  
  framework to complete the workflow    
  use existing language and framework   
  don't change the workflow your part   
  is creat a voice_input and transfer   
  the output to llm



  In vtop_vitap_agent create a file     
  name llm.py and use lanchain and langchain-mcp  
  framework and dont change the language use python  to integrate ollama model as well the mcp server form vtop_mcp_tool 
  to connect the tool so the llm can use it effectively


  In vtop_vitap_agent create a file     
  name voice_output.py and use kokoro  
  framework to complete the workflow    
  use existing language and framework   
  don't change the workflow your part
  is to get output message from the llm.py and send
  back the voice maessage to the user 




  # workflow

  user-make a call on the dummy number the service - > windsurf use user preferd natural language like , english , hindhi , telugu and tamil -> llm get the user query and process the query and send the output to the -> voice_output (kokoro)
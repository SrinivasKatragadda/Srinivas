import 'dotenv/config'

const payload = {
  subflowSpec: {
    id: "test_subflow_id",
    name: "Test Subflow",
    description: "A test subflow",
    inputs: {
      myInput: {
        type: "string",
        label: "My Input",
        mandatory: true
      }
    },
    outputs: {
      myOutput: {
        type: "integer",
        label: "My Output"
      }
    },
    actions: [
      {
        id: "log_action",
        type: "core.log",
        inputs: {
          log_level: "info",
          log_message: {
            template: "Hello {{input.myInput}}"
          }
        }
      }
    ]
  }
}

async function test() {
  console.log("Generating subflow source...");
  const { generateSubflowSource } = await import('./src/generator/generateSubflowSource.js');
  const source = generateSubflowSource(payload.subflowSpec as any);
  console.log("Generated source:\n" + source);
}

test().catch(console.error);

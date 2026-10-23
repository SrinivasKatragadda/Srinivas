export const availableActions = [
  {
    type: 'subflow.call',
    description: 'Call a deployed subflow (by sys_id) and wait for it to finish',
    snippet: {
      type: 'subflow.call',
      inputs: {
        subflow_sys_id: '0123456789abcdef0123456789abcdef',
        subflow_inputs: {
          record_id: '{{trigger.current.sys_id}}',
        },
        wait_for_completion: true,
      },
    },
  },
  {
    type: 'core.addWorknoteLinkToContext',
    description: 'Add a work note with a link to the flow context',
    snippet: {
      type: 'core.addWorknoteLinkToContext',
      inputs: {
        journal_field: 'work_notes',
        record: '{{trigger.current.sys_id}}',
        table: 'incident',
        additional_comments: 'Flow context link added by SDK test flow',
      },
    },
  },
  {
    type: 'core.askForApproval',
    description: 'Ask for approval on a record',
    snippet: {
      type: 'core.askForApproval',
      inputs: {
        approval_field: 'approval',
        journal_field: 'work_notes',
        table: 'incident',
        record: '{{trigger.current.sys_id}}',
        approval_reason: 'SDK approval action test',
        approval_conditions: {
          conditionType: 'OR',
          ruleSets: [
            {
              action: 'ApprovesRejects',
              conditionType: 'AND',
              rules: [
                [
                  {
                    ruleType: 'Any',
                    users: ['{{action.lookup_admin_user.Record.sys_id}}'],
                    groups: [],
                    manual: false,
                  },
                ],
              ],
            },
          ],
        },
      },
    },
  },
  {
    type: 'core.associateRecordToEmail',
    description: 'Associate a target record to an email record',
    snippet: {
      type: 'core.associateRecordToEmail',
      inputs: {
        email_record: '{{action.lookup_email_record.Record.sys_id}}',
        target_record: '{{action.create_incident.record}}',
      },
    },
  },
  {
    type: 'core.copyAttachment',
    description: 'Copy one attachment to a target record',
    snippet: {
      type: 'core.copyAttachment',
      inputs: {
        attachment_record: '{{action.resolve_lookup_attachment_record.Record}}',
        table: 'incident_task',
        target_record: '{{action.create_incident_task.Record}}',
      },
    },
  },
  {
    type: 'core.createCatalogTask',
    description: 'Create a catalog task for a requested item',
    snippet: {
      type: 'core.createCatalogTask',
      inputs: {
        ah_requested_item: '{{action.submit_catalog_item_request.requested_item}}',
        ah_short_description: 'Fulfill SDK request',
        ah_table_name: 'sc_task',
        ah_wait: false,
        ah_fields: {
          short_description: 'Fulfill SDK request',
        },
      },
    },
  },
  {
    type: 'core.createOrUpdateRecord',
    description: 'Create or update a record in a table',
    snippet: {
      type: 'core.createOrUpdateRecord',
      inputs: {
        table_name: 'incident',
        fields: {
          short_description: 'Created or updated by SDK Flow',
        },
      },
    },
  },
  {
    type: 'core.createRecord',
    description: 'Create a new record in a table',
    snippet: {
      type: 'core.createRecord',
      inputs: {
        table_name: 'incident',
        values: {
          short_description: 'Created by SDK Flow',
        },
      },
    },
  },
  {
    type: 'core.createTask',
    description: 'Create a task record',
    snippet: {
      type: 'core.createTask',
      inputs: {
        task_table: 'incident_task',
        wait: false,
        field_values: {
          short_description: 'Follow-up task',
          parent: '{{action.create_incident.record}}',
        },
      },
    },
  },
  {
    type: 'core.deleteAttachment',
    description: 'Delete attachments on a record',
    snippet: {
      type: 'core.deleteAttachment',
      inputs: {
        delete_all_: false,
        attachment_file_name: 'sdk-action-test.txt',
        record: '{{trigger.current.sys_id}}',
        table: 'incident',
      },
    },
  },
  {
    type: 'core.deleteRecord',
    description: 'Delete a record',
    snippet: {
      type: 'core.deleteRecord',
      inputs: {
        record: '{{action.create_incident.record}}',
      },
    },
  },
  {
    type: 'core.fireEvent',
    description: 'Fire a registered event',
    snippet: {
      type: 'core.fireEvent',
      inputs: {
        event_name: 'incident.inserted',
        table: 'incident',
        record: '{{action.create_incident.record}}',
        parm1: 'value1',
        parm2: 'value2',
      },
    },
  },
  {
    type: 'core.getAttachmentsOnRecord',
    description: 'Get attachments associated with a record',
    snippet: {
      type: 'core.getAttachmentsOnRecord',
      inputs: {
        source_record: '{{trigger.current.sys_id}}',
        file_name: 'sdk-action-test.txt',
      },
    },
  },
  {
    type: 'core.getCatalogVariables',
    description: 'Get variables from a requested item',
    snippet: {
      type: 'core.getCatalogVariables',
      inputs: {
        requested_item: '{{action.submit_catalog_item_request.requested_item}}',
        template_catalog_item: '{{action.lookup_catalog_item.Record.sys_id}}',
        catalog_variables: [],
      },
    },
  },
  {
    type: 'core.getEmailHeader',
    description: 'Get a header value from an email record',
    snippet: {
      type: 'core.getEmailHeader',
      inputs: {
        email_record: '{{action.lookup_email_record.Record.sys_id}}',
        target_header: 'In-Reply-To',
      },
    },
  },
  {
    type: 'core.getLatestResponseTextFromEmail',
    description: 'Get the latest response text from an email thread',
    snippet: {
      type: 'core.getLatestResponseTextFromEmail',
      inputs: {
        email_record: '{{action.lookup_email_record.Record.sys_id}}',
      },
    },
  },
  {
    type: 'core.log',
    description: 'Write a message to the system log',
    snippet: {
      type: 'core.log',
      inputs: {
        log_level: 'info',
        log_message: 'Flow executed successfully',
      },
    },
  },
  {
    type: 'core.lookUpEmailAttachments',
    description: 'Look up attachments associated with an email record',
    snippet: {
      type: 'core.lookUpEmailAttachments',
      inputs: {
        email_record: '{{action.lookup_email_record.Record.sys_id}}',
      },
    },
  },
  {
    type: 'core.lookUpRecord',
    description: 'Look up a single record',
    snippet: {
      type: 'core.lookUpRecord',
      inputs: {
        table: 'sys_user',
        conditions: 'user_name=admin',
        sort_type: 'sort_asc',
        if_multiple_records_are_found_action: 'use_first_record',
      },
    },
  },
  {
    type: 'core.lookUpRecords',
    description: 'Look up multiple records',
    snippet: {
      type: 'core.lookUpRecords',
      inputs: {
        table: 'incident',
        conditions: 'active=true',
        max_results: 10,
      },
    },
  },
  {
    type: 'core.lookupAttachment',
    description: 'Look up an attachment record by source record and file name',
    snippet: {
      type: 'core.lookupAttachment',
      inputs: {
        source_record: '{{trigger.current.sys_id}}',
        file_name: 'sdk-action-test.txt',
      },
    },
  },
  {
    type: 'core.moveAttachment',
    description: 'Move one attachment to a target record',
    snippet: {
      type: 'core.moveAttachment',
      inputs: {
        source_attachment_record: '{{action.resolve_lookup_attachment_record.Record}}',
        table: 'incident_task',
        target_record: '{{action.create_incident_task.Record}}',
      },
    },
  },
  {
    type: 'core.moveEmailAttachmentsToRecord',
    description: 'Move all email attachments to a target record',
    snippet: {
      type: 'core.moveEmailAttachmentsToRecord',
      inputs: {
        email_record: '{{action.lookup_email_record.Record.sys_id}}',
        target_record: '{{action.create_incident.record}}',
      },
    },
  },
  {
    type: 'core.recordProducer',
    description: 'Submit a record producer',
    snippet: {
      type: 'core.recordProducer',
      inputs: {
        catalog_item: '{{action.lookup_record_producer.Record.sys_id}}',
        catalog_item_inputs: 'short_description=Record producer test',
        _snc_dont_fail_on_error: true,
      },
    },
  },
  {
    type: 'core.sendEmail',
    description: 'Send an email',
    snippet: {
      type: 'core.sendEmail',
      inputs: {
        table_name: 'incident',
        record: '{{action.create_incident.record}}',
        watermark_email: true,
        ah_to: '{{action.lookup_admin_user.Record.email}}',
        ah_subject: 'Incident Update',
        ah_body: 'The incident has been updated.',
      },
    },
  },
  {
    type: 'core.sendNotification',
    description: 'Send a notification via a notification record',
    snippet: {
      type: 'core.sendNotification',
      inputs: {
        table_name: 'incident',
        record: '{{action.create_incident.record}}',
        notification: '00000000000000000000000000000000',
      },
    },
  },
  {
    type: 'core.sendSms',
    description: 'Send an SMS message',
    snippet: {
      type: 'core.sendSms',
      inputs: {
        recipients: '+1234567890',
        message: 'Alert text',
      },
    },
  },
  {
    type: 'core.slaPercentageTimer',
    description: 'Wait for a percentage of an SLA to complete',
    snippet: {
      type: 'core.slaPercentageTimer',
      inputs: {
        percentage: 50,
        task_sla_record: '{{action.lookup_task_sla.Record}}',
      },
    },
  },
  {
    type: 'core.submitCatalogItemRequest',
    description: 'Submit a request for a catalog item',
    snippet: {
      type: 'core.submitCatalogItemRequest',
      inputs: {
        catalog_item: '{{action.lookup_catalog_item.Record.sys_id}}',
        sysparm_requested_for: '{{action.lookup_admin_user.Record.sys_id}}',
        sysparm_quantity: 1,
        wait_for_completion: false,
        _snc_dont_fail_on_error: true,
      },
    },
  },
  {
    type: 'core.updateMultipleRecords',
    description: 'Update multiple records matching a condition',
    snippet: {
      type: 'core.updateMultipleRecords',
      inputs: {
        table_name: 'incident',
        conditions: 'active=true',
        field_values: {
          state: '2',
        },
        dont_fail_flow_on_error: true,
      },
    },
  },
  {
    type: 'core.updateRecord',
    description: 'Update an existing record',
    snippet: {
      type: 'core.updateRecord',
      inputs: {
        table: 'incident',
        record: '{{trigger.current.sys_id}}',
        fields: {
          state: '2',
        },
      },
    },
  },
  {
    type: 'core.waitForCondition',
    description: 'Pause the flow until a record meets a condition',
    snippet: {
      type: 'core.waitForCondition',
      inputs: {
        table_name: 'incident',
        record: '{{action.create_incident.record}}',
        conditions: 'state=3',
        timeout_flag: false,
      },
    },
  },
  {
    type: 'core.waitForEmailReply',
    description: 'Pause the flow until an email reply is received',
    snippet: {
      type: 'core.waitForEmailReply',
      inputs: {
        record: '{{action.lookup_email_record.Record}}',
        enable_timeout: false,
      },
    },
  },
  {
    type: 'core.waitForMessage',
    description: 'Pause the flow until a Flow API message is received',
    snippet: {
      type: 'core.waitForMessage',
      inputs: {
        message: 'sdk-action-test',
        enable_timeout: false,
      },
    },
  },
]
